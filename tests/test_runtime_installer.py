"""Fetching an engine's runtime on demand.

Some engines need a Python package the .deb cannot carry, because it is
compiled against one CPython ABI. Rather than offering an engine that can
only fail, the app installs the runtime when the engine is chosen — into a
directory it owns, because Debian's Python refuses to be written to.
"""

from __future__ import annotations

import sys

import pytest

from fluentry.persistence.speech_model import SpeechBackend, SpeechModel
from fluentry.services import runtime_installer
from fluentry.services.runtime_installer import (
    FASTER_WHISPER,
    ONNX_ASR,
    RUNTIMES,
    Runtime,
    activate_installed_runtimes,
    install,
    runtime_for,
)


def test_an_engine_that_works_asks_for_nothing(monkeypatch):
    """`runtime_for` answers "there is something to install", or None."""
    monkeypatch.setattr(Runtime, "is_installed", lambda self: True)
    for model in SpeechModel.available_models():
        assert runtime_for(model) is None


def test_whisper_asks_for_faster_whisper_when_nothing_can_run_it(monkeypatch):
    from fluentry.services.providers.whisper import WhisperCppProvider

    monkeypatch.setattr(Runtime, "is_installed", lambda self: False)
    monkeypatch.setattr(WhisperCppProvider, "is_available", classmethod(lambda cls: False))

    whisper = next(m for m in SpeechModel if m.backend is SpeechBackend.WHISPER_CPP)
    assert runtime_for(whisper) is FASTER_WHISPER


def test_an_existing_whisper_cpp_binary_is_used_rather_than_downloading(monkeypatch):
    """150 MB should not be fetched to replace something already present."""
    from fluentry.services.providers.whisper import WhisperCppProvider

    monkeypatch.setattr(Runtime, "is_installed", lambda self: False)
    monkeypatch.setattr(WhisperCppProvider, "is_available", classmethod(lambda cls: True))

    whisper = next(m for m in SpeechModel if m.backend is SpeechBackend.WHISPER_CPP)
    assert runtime_for(whisper) is None


def test_every_runtime_says_what_it_costs_before_it_is_fetched():
    """The user agrees to a download; they should be told its size."""
    for runtime in RUNTIMES:
        assert runtime.megabytes > 0
        assert runtime.packages
        assert runtime.provides


def test_a_runtime_is_installed_when_its_modules_import(monkeypatch):
    assert Runtime(
        key="k", name="n", packages=("p",), megabytes=1, provides=("sys", "os")
    ).is_installed()
    assert not Runtime(
        key="k", name="n", packages=("p",), megabytes=1, provides=("no_such_module_xyz",)
    ).is_installed()


def test_activation_shadows_the_runtime_it_replaces(monkeypatch, tmp_path):
    """An installed runtime goes to the FRONT of the path.

    The rule used to be the polite opposite - append, so the distribution
    wins - until Ubuntu shipped an onnxruntime that loads the model and
    transcribes every recording to an empty string. A runtime is only
    installed here because the system one is missing or has been caught
    doing that, and a replacement that loses the import race to the thing
    it replaces fixes nothing.
    """
    packages = tmp_path / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    packages.mkdir(parents=True)
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path))
    before = list(sys.path)
    try:
        assert activate_installed_runtimes()
        assert sys.path[0] == str(packages)
    finally:
        sys.path[:] = before


def test_activation_ignores_a_runtime_that_was_never_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path / "absent"))
    before = list(sys.path)
    try:
        assert activate_installed_runtimes() == []
        assert sys.path == before
    finally:
        sys.path[:] = before


def test_a_failed_install_explains_itself_rather_than_raising(monkeypatch, tmp_path):
    """The caller shows this to the user, so it must be a sentence."""
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path / "rt"))
    monkeypatch.setattr(runtime_installer, "_run", lambda command, timeout: "no network")

    failure = install(FASTER_WHISPER)
    assert failure is not None
    assert "no network" in failure


def test_progress_is_reported_before_each_slow_step(monkeypatch, tmp_path):
    """A 150 MB download with no explanation looks like a hang."""
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path / "rt"))
    monkeypatch.setattr(runtime_installer, "_run", lambda command, timeout: "stopped")

    messages: list[str] = []
    install(FASTER_WHISPER, on_progress=messages.append)
    assert messages, "the user is told what is happening before it happens"
    assert any("faster-whisper" in m for m in messages)


@pytest.mark.slow
def test_a_runtime_really_installs_and_imports(tmp_path, monkeypatch):
    """The whole thing, against PyPI.

    Marked slow because it downloads; it is the only test that proves the
    venv is created correctly, that Debian's externally-managed Python does
    not refuse it, and that the result is importable afterwards.
    """
    tiny = Runtime(
        key="test", name="wrapt", packages=("wrapt",), megabytes=1, provides=("wrapt",)
    )
    monkeypatch.setattr(Runtime, "directory", property(lambda self: tmp_path / "rt"))

    assert install(tiny) is None
    assert tiny.site_packages is not None
    assert (tiny.site_packages / "wrapt").exists()
