"""A speech runtime is only trusted after it transcribes something.

Ubuntu 26.04's python3-onnxruntime loads the Parakeet model without
complaint and returns an empty string for every recording - no exception,
no warning, `import onnxruntime` succeeds. Four seconds of clear speech at
a healthy peak produced zero characters, on every attempt, and every check
short of actually transcribing said the runtime was fine. So the check is
actually transcribing: a bundled recording whose words are known.
"""

from __future__ import annotations

import pytest

from fluentry.services import runtime_verification
from fluentry.services.runtime_verification import (
    CHECK_RECORDING,
    known_verdict,
    verify,
)


class HearsNothing:
    """Ubuntu's runtime, in one line: runs fine, says nothing."""

    def transcribe(self, samples, language=None):
        class Result:
            text = ""

        return Result()


class HearsTheWords:
    def transcribe(self, samples, language=None):
        class Result:
            text = "Hello Fluid Voice"

        return Result()


class HearsSomethingElse:
    """A runtime that mangles audio is as faulty as one that drops it."""

    def transcribe(self, samples, language=None):
        class Result:
            text = "aaaa bbbb cccc"

        return Result()


def test_the_check_recording_ships_with_the_app():
    """No recording, no verification, and the whole idea silently dies."""
    assert CHECK_RECORDING.exists(), CHECK_RECORDING
    assert CHECK_RECORDING.stat().st_size > 10_000


def test_a_runtime_that_transcribes_nothing_is_ruled_faulty():
    assert verify(HearsNothing(), fingerprint="test-nothing") is False
    assert known_verdict("test-nothing") is False


def test_a_runtime_that_hears_the_words_is_trusted():
    assert verify(HearsTheWords(), fingerprint="test-words") is True
    assert known_verdict("test-words") is True


def test_a_runtime_that_hears_the_wrong_words_is_ruled_faulty():
    assert verify(HearsSomethingElse(), fingerprint="test-garbled") is False


def test_the_verdict_is_remembered_rather_than_re_run():
    """The check costs a real transcription; once per runtime is enough."""
    calls = []

    class Counting:
        def transcribe(self, samples, language=None):
            calls.append(1)

            class Result:
                text = "hello fluid voice"

            return Result()

    provider = Counting()
    assert verify(provider, fingerprint="test-cached") is True
    assert verify(provider, fingerprint="test-cached") is True
    assert len(calls) == 1


def test_a_replaced_runtime_is_rechecked():
    """The verdict is keyed to the build, not to the machine."""
    assert verify(HearsNothing(), fingerprint="ort 1.23 at /usr/lib") is False
    assert verify(HearsTheWords(), fingerprint="ort 1.30 at /home/app") is True
    assert known_verdict("ort 1.23 at /usr/lib") is False
    assert known_verdict("ort 1.30 at /home/app") is True


def test_a_check_that_blows_up_counts_as_faulty():
    class Explodes:
        def transcribe(self, samples, language=None):
            raise RuntimeError("segfault-adjacent")

    assert verify(Explodes(), fingerprint="test-explodes") is False


def test_a_faulty_system_runtime_reads_as_a_missing_one(monkeypatch, settings):
    """The engine screens must offer the fix, not the broken status quo.

    "Importable" and "working" came apart on Ubuntu; to the user a runtime
    that transcribes nothing IS a missing runtime, so runtime_for reports
    it as one - unless the app's own copy is already installed.
    """
    from fluentry.persistence.speech_model import SpeechModel
    from fluentry.services import runtime_installer
    from fluentry.services.runtime_installer import ONNX_ASR, Runtime, runtime_for

    monkeypatch.setattr(
        runtime_verification, "known_verdict", lambda fp=None: False
    )
    monkeypatch.setattr(Runtime, "is_privately_installed", lambda self: False)
    assert runtime_for(SpeechModel.default_model()) is ONNX_ASR

    monkeypatch.setattr(Runtime, "is_privately_installed", lambda self: True)
    assert runtime_for(SpeechModel.default_model()) is None


def test_the_private_runtime_is_installed_even_over_a_satisfying_system_one(
    monkeypatch, tmp_path
):
    """pip would skip onnxruntime: 1.23 satisfies ">=1.17" and is broken."""
    from fluentry.services import runtime_installer
    from fluentry.services.runtime_installer import ONNX_ASR, Runtime, install

    import sys

    root = tmp_path / "rt"
    # A venv that already exists, so install() goes straight to pip.
    (root / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages").mkdir(
        parents=True
    )
    (root / "bin").mkdir()
    (root / "bin" / "pip").touch()

    commands = []
    monkeypatch.setattr(Runtime, "directory", property(lambda self: root))
    monkeypatch.setattr(
        runtime_installer, "_run",
        lambda command, timeout: commands.append(command) or "stop after recording",
    )
    install(ONNX_ASR)
    pip_calls = [c for c in commands if "install" in c]
    assert pip_calls and "--ignore-installed" in pip_calls[-1]


def test_a_restart_is_reported_when_the_broken_runtime_is_already_loaded(monkeypatch, tmp_path):
    """A native extension cannot be swapped once imported.

    Detecting the faulty runtime imports it, so by the time the replacement
    is installed the broken build is in sys.modules and answering every
    `import onnxruntime`. It cannot be evicted - onnxruntime registers its
    schema in C++ on first import and a second build collides - so the app
    installs it, uses it next launch, and says so.
    """
    import sys
    import types

    from fluentry.services.runtime_installer import (
        ONNX_ASR,
        Runtime,
        already_loaded_from_elsewhere,
    )

    private = tmp_path / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    private.mkdir(parents=True)
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path))

    stale = types.ModuleType("onnxruntime")
    stale.__file__ = "/usr/lib/python3/dist-packages/onnxruntime/__init__.py"
    before = dict(sys.modules)
    sys.modules["onnxruntime"] = stale
    try:
        assert already_loaded_from_elsewhere(ONNX_ASR) is True, (
            "a build loaded from outside the private copy needs a restart"
        )
    finally:
        sys.modules.clear()
        sys.modules.update(before)


def test_no_restart_needed_when_nothing_was_loaded_yet(monkeypatch, tmp_path):
    import sys

    from fluentry.services.runtime_installer import ONNX_ASR, Runtime, already_loaded_from_elsewhere

    private = tmp_path / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    private.mkdir(parents=True)
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path))

    before = dict(sys.modules)
    for name in list(sys.modules):
        if name.split(".", 1)[0] in ONNX_ASR.provides:
            del sys.modules[name]
    try:
        assert already_loaded_from_elsewhere(ONNX_ASR) is False
    finally:
        sys.modules.clear()
        sys.modules.update(before)
