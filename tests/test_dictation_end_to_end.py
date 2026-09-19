"""Port of `testDictationEndToEnd_whisperTiny_transcribesFixture`.

Runs the real Whisper Tiny model over the shared audio fixture, through
the same pipeline the app uses, and checks the text that would actually
be typed.

Marked `model` and deselected by default because it needs the weights on
disk. Run it with:

    pytest -m model
"""

from pathlib import Path

import pytest

from fluentry.persistence.settings_types import CustomDictionaryEntry
from fluentry.persistence.speech_model import SpeechModel
from fluentry.platform.text_injection import RecordingBackend, TypingService
from fluentry.services.asr_service import ASRService
from fluentry.services.providers.whisper import FasterWhisperProvider, read_wav_as_mono_float
from fluentry.services.text_pipeline import PipelineContext

FIXTURE = Path(__file__).parent / "resources" / "dictation_fixture.wav"

#: The model cache is deliberately the user's real one, not the per-test
#: sandbox, so the weights are downloaded at most once on this machine.
MODEL_CACHE = Path.home() / ".cache" / "fluentry" / "models" / "whisper"

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def whisper_tiny():
    if not FasterWhisperProvider.is_available():
        pytest.skip("faster-whisper is not installed")
    provider = FasterWhisperProvider(model=SpeechModel.WHISPER_TINY, download_root=MODEL_CACHE)
    try:
        provider.prepare()
    except Exception as error:  # offline, or no weights cached yet
        pytest.skip(f"Whisper Tiny is unavailable: {error}")
    return provider


def test_fixture_loads_as_16k_mono(whisper_tiny):
    samples = read_wav_as_mono_float(FIXTURE)
    assert 0.5 < len(samples) / 16_000 < 5.0
    assert max(abs(sample) for sample in samples) > 0.1


def test_dictation_end_to_end_whisper_tiny_transcribes_fixture(whisper_tiny):
    samples = read_wav_as_mono_float(FIXTURE)
    result = whisper_tiny.transcribe(samples, language="en")

    assert result.text, "the fixture must produce some transcript"
    assert "hello" in result.text.lower()
    assert "fluid" in result.text.lower()
    assert result.duration_milliseconds >= 0


def test_dictation_end_to_end_types_dictionary_corrected_text(whisper_tiny, settings, tmp_path):
    """The fixture is misheard as "fluid boys"; the dictionary fixes it."""
    from fluentry.persistence.history_database import TranscriptionHistoryWriter
    from fluentry.persistence.history_store import TranscriptionHistoryStore

    settings.custom_dictionary_entries = [
        CustomDictionaryEntry(triggers=["fluid boys", "fluid voice"], replacement="Fluentry")
    ]
    backend = RecordingBackend()
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    history = TranscriptionHistoryStore(writer=writer)
    history.wait_until_loaded()

    service = ASRService(
        settings=settings,
        provider=whisper_tiny,
        typing_service=TypingService(backend=backend, paste_settle_seconds=0),
        history_store=history,
        focus_context=lambda: PipelineContext(app_name="Editor", window_title="notes.txt"),
    )

    outcome = service.process_samples(read_wav_as_mono_float(FIXTURE))

    assert outcome.error is None
    assert "Fluentry" in outcome.final_text
    assert backend.typed == [outcome.final_text]
    assert history.entries[0].processed_text == outcome.final_text
    assert history.entries[0].raw_text == outcome.raw_text
    writer.shutdown()
