"""Trust a speech runtime only after hearing it transcribe.

Ubuntu 26.04's python3-onnxruntime loads the Parakeet model without a
murmur and returns an empty string for every recording. Nothing raises,
nothing logs on their side, `import onnxruntime` succeeds — every check
short of actually transcribing something says the runtime is fine. So the
check is actually transcribing something: a bundled recording whose words
are known, run once per runtime and remembered.

The verdict is cached against the runtime's version and location, so a
system update that swaps the runtime out re-earns trust on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..logging_setup import get_logger

_log = get_logger("verify")

#: What the bundled recording says; any of these words counts as heard.
EXPECTED_WORDS = ("hello", "fluid", "voice")

CHECK_RECORDING = Path(__file__).resolve().parent.parent / "resources" / "runtime_check.wav"


def _verdicts_path() -> Path:
    from ..persistence.defaults import state_home

    return state_home() / "runtime-verdicts.json"


def runtime_fingerprint() -> str:
    """Identifies the runtime build, so a replaced one is re-checked."""
    try:
        import onnxruntime

        return f"onnxruntime {onnxruntime.__version__} at {onnxruntime.__file__}"
    except Exception as error:
        return f"unimportable: {error}"


def _read_verdicts() -> dict:
    try:
        return json.loads(_verdicts_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_verdict(fingerprint: str, is_sound: bool) -> None:
    verdicts = _read_verdicts()
    verdicts[fingerprint] = is_sound
    try:
        path = _verdicts_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(verdicts, indent=2))
    except OSError as error:
        _log.warning("could not remember the runtime verdict: %s", error)


def known_verdict(fingerprint: str | None = None) -> bool | None:
    """True (sound), False (faulty) or None (never checked)."""
    return _read_verdicts().get(fingerprint or runtime_fingerprint())


def verify(provider, fingerprint: str | None = None) -> bool:
    """Transcribe the bundled recording; remember and return the verdict.

    `provider` must already be prepared. A sound runtime hears at least one
    of the known words; one that returns nothing, or something entirely
    unrelated, is recorded as faulty so the engine screens can offer a
    working replacement instead of an engine that pretends to work.
    """
    fingerprint = fingerprint or runtime_fingerprint()
    cached = known_verdict(fingerprint)
    if cached is not None:
        return cached

    from .providers.whisper import read_wav_as_mono_float

    try:
        samples = read_wav_as_mono_float(CHECK_RECORDING)
        heard = provider.transcribe(samples, language=None).text or ""
    except Exception as error:
        _log.error("the runtime check itself failed: %s", error)
        _write_verdict(fingerprint, False)
        return False

    is_sound = any(word in heard.lower() for word in EXPECTED_WORDS)
    if is_sound:
        _log.info("runtime verified: %s heard %r", fingerprint, heard)
    else:
        _log.error(
            "FAULTY RUNTIME: %s transcribed the known recording as %r "
            "(expected one of %s). It loads and runs but does not work.",
            fingerprint,
            heard,
            ", ".join(EXPECTED_WORDS),
        )
    _write_verdict(fingerprint, is_sound)
    return is_sound
