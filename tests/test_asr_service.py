"""Port of the silence-gate coverage plus an end-to-end dictation round trip."""

import math

import pytest

from fluentry.persistence.history_store import TranscriptionHistoryStore
from fluentry.persistence.settings_store import SettingsStore
from fluentry.persistence.settings_types import (
    CustomDictionaryEntry,
    TextInsertionMode,
)
from fluentry.platform.clipboard import InMemoryClipboard
from fluentry.platform.text_injection import RecordingBackend, TypingService
from fluentry.services.asr_service import (
    ASRService,
    assess_short_audio_silence,
    should_assess_short_audio_silence,
)
from fluentry.services.providers.base import (
    ScriptedTranscriptionProvider,
    TranscriptionProviderError,
)
from fluentry.services.text_pipeline import PipelineContext


# --- silence gate -----------------------------------------------------------


def test_short_audio_silence_gate_rejects_only_clear_short_silence():
    silence = [0.0005] * 16_000
    assessment = assess_short_audio_silence(silence)
    assert assessment.is_eligible
    assert assessment.should_skip_transcription

    quiet_speech = [0.0005] * 16_000
    for index in range(4000, 4320):
        quiet_speech[index] = 0.012 if index % 2 == 0 else -0.012
    quiet = assess_short_audio_silence(quiet_speech)
    assert quiet.is_eligible
    assert not quiet.should_skip_transcription, "quiet speech must still be transcribed"

    long_silence = [0.0] * 64_001
    long_assessment = assess_short_audio_silence(long_silence)
    assert not long_assessment.is_eligible
    assert not long_assessment.should_skip_transcription


def test_short_audio_silence_gate_fails_open_for_invalid_samples():
    samples = [0.0] * 8000
    samples[100] = math.nan

    assessment = assess_short_audio_silence(samples)
    assert assessment.is_eligible
    assert not assessment.should_skip_transcription


def test_short_audio_silence_gate_reports_measurements():
    samples = [0.0] * 1600
    samples[10] = 0.5
    assessment = assess_short_audio_silence(samples)

    assert assessment.duration_milliseconds == 100
    assert assessment.peak_amplitude == pytest.approx(0.5)
    assert assessment.rms_amplitude > 0
    assert assessment.maximum_frame_rms > 0


def test_short_audio_silence_gate_rejects_an_empty_buffer():
    assessment = assess_short_audio_silence([])
    assert not assessment.is_eligible
    assert assessment.duration_milliseconds == 0


def test_short_audio_silence_gate_runs_only_when_enabled_for_unrecognized_dictation():
    assert not should_assess_short_audio_silence(False, False, False)
    assert not should_assess_short_audio_silence(True, True, False)
    assert not should_assess_short_audio_silence(True, False, True)
    assert should_assess_short_audio_silence(True, False, False)


# --- dictation round trip ---------------------------------------------------


@pytest.fixture
def dictation(settings: SettingsStore, tmp_path):
    from fluentry.persistence.history_database import TranscriptionHistoryWriter

    provider = ScriptedTranscriptionProvider()
    backend = RecordingBackend()
    clipboard = InMemoryClipboard()
    typing = TypingService(backend=backend, clipboard=clipboard, paste_settle_seconds=0)
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    history = TranscriptionHistoryStore(writer=writer)
    history.wait_until_loaded()

    service = ASRService(
        settings=settings,
        provider=provider,
        pipeline=None,
        typing_service=typing,
        clipboard=clipboard,
        history_store=history,
        focus_context=lambda: PipelineContext(app_name="Editor", window_title="notes.txt"),
    )
    yield service, provider, backend, clipboard, history
    writer.shutdown()


def test_dictation_transcribes_formats_and_types(dictation, settings):
    service, provider, backend, clipboard, history = dictation
    provider.prepare()
    provider.responses = ["hello fluid boys"]
    settings.custom_dictionary_entries = [
        CustomDictionaryEntry(triggers=["fluid boys"], replacement="Fluentry")
    ]

    outcome = service.process_samples([0.2] * 100)

    assert outcome.raw_text == "hello fluid boys"
    assert outcome.final_text == "hello Fluentry"
    assert backend.typed == ["hello Fluentry"]
    assert outcome.entry is not None
    assert history.entries[0].processed_text == "hello Fluentry"


def test_dictation_applies_spoken_punctuation_before_typing(dictation, settings):
    service, provider, backend, _, _ = dictation
    provider.prepare()
    provider.responses = ["hello literal comma world"]

    service.process_samples([0.2] * 100)

    assert backend.typed == ["hello, world"]


def test_dictation_skips_a_silent_clip_without_calling_the_provider(dictation, settings):
    service, provider, backend, _, history = dictation
    provider.prepare()
    provider.responses = ["should never be used"]
    settings.skip_silent_recordings_enabled = True

    outcome = service.process_samples([0.0] * 16_000)

    assert outcome.was_skipped_as_silent
    assert outcome.final_text == ""
    assert backend.typed == []
    assert provider.received_samples == []
    assert history.entries == []


def test_dictation_reports_a_provider_failure_without_typing(dictation):
    service, provider, backend, _, _ = dictation
    provider.prepare()
    provider.failure = TranscriptionProviderError("model exploded")

    outcome = service.process_samples([0.2] * 100)

    assert outcome.error == "model exploded"
    assert backend.typed == []


def test_dictation_copies_to_the_clipboard_when_enabled(dictation, settings):
    service, provider, backend, clipboard, _ = dictation
    provider.prepare()
    provider.responses = ["copy me"]
    settings.copy_transcription_to_clipboard = True

    service.process_samples([0.2] * 100)

    assert clipboard.read_text() == "copy me"


def test_dictation_falls_back_to_the_transcript_when_enhancement_fails(settings, tmp_path):
    from fluentry.persistence.history_database import TranscriptionHistoryWriter

    provider = ScriptedTranscriptionProvider()
    provider.prepare()
    provider.responses = ["raw words"]
    backend = RecordingBackend()
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    history = TranscriptionHistoryStore(writer=writer)
    history.wait_until_loaded()

    def broken_enhance(text, context):
        raise RuntimeError("provider unreachable")

    settings.enable_ai_processing = True
    service = ASRService(
        settings=settings,
        provider=provider,
        typing_service=TypingService(backend=backend, paste_settle_seconds=0),
        history_store=history,
        enhance=broken_enhance,
    )

    outcome = service.process_samples([0.2] * 100)

    assert outcome.ai_processing_error == "provider unreachable"
    assert not outcome.was_ai_processed
    assert outcome.final_text == "raw words"
    assert backend.typed == ["raw words"]
    assert history.entries[0].ai_processing_error == "provider unreachable"
    writer.shutdown()


def test_dictation_uses_the_enhanced_text_when_enhancement_succeeds(settings, tmp_path):
    from fluentry.persistence.history_database import TranscriptionHistoryWriter

    provider = ScriptedTranscriptionProvider()
    provider.prepare()
    provider.responses = ["raw words"]
    backend = RecordingBackend()
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    history = TranscriptionHistoryStore(writer=writer)
    history.wait_until_loaded()

    settings.enable_ai_processing = True
    service = ASRService(
        settings=settings,
        provider=provider,
        typing_service=TypingService(backend=backend, paste_settle_seconds=0),
        history_store=history,
        enhance=lambda text, context: "Raw words.",
    )

    outcome = service.process_samples([0.2] * 100)

    assert outcome.was_ai_processed
    assert outcome.final_text == "Raw words."
    assert history.entries[0].was_ai_processed
    writer.shutdown()


def test_spoken_send_strips_the_phrase_and_presses_the_send_key(dictation, settings):
    service, provider, backend, _, _ = dictation
    provider.prepare()
    provider.responses = ["ship this today send it"]
    settings.spoken_send_enabled = True

    outcome = service.process_samples([0.2] * 100)

    assert outcome.did_send
    assert outcome.final_text == "ship this today."
    assert backend.typed == ["ship this today."]
    assert backend.chords[-1][0] == "Return"


def test_paste_last_transcription_uses_the_clipboard_path(dictation, settings):
    service, provider, backend, clipboard, history = dictation
    provider.prepare()
    provider.responses = ["remembered text"]
    service.process_samples([0.2] * 100)

    backend.typed.clear()
    backend.chords.clear()
    assert service.paste_last_transcription()
    assert backend.chords == [("v", ("ctrl",))]


def test_recording_lifecycle_tracks_state(settings):
    class Backend:
        def __init__(self):
            self.started = False
            self.handler = None

        def start(self, device_uid, on_pcm):
            self.started = True
            self.handler = on_pcm

        def stop(self):
            self.started = False

        @property
        def is_running(self):
            return self.started

    provider = ScriptedTranscriptionProvider()
    provider.prepare()
    provider.responses = ["captured"]
    capture = Backend()
    service = ASRService(settings=settings, provider=provider, capture_backend=capture)

    states: list[str] = []
    service.add_state_observer(states.append)

    assert service.start_recording()
    assert service.is_running and capture.started
    assert not service.start_recording(), "a second start is ignored"

    capture.handler([0.3, -0.3], 0.3, 0.3)
    assert service.buffer.count == 2

    outcome = service.stop_recording_and_transcribe()
    assert outcome.raw_text == "captured"
    assert not service.is_running and not capture.started
    # "inserting" is its own state: the overlay has to come down before the
    # text is inserted, or it holds the focus and the text lands on it.
    assert states == ["recording", "transcribing", "inserting", "idle"]


def test_cancelling_a_recording_discards_the_buffer(settings):
    class Backend:
        def __init__(self):
            self.started = False
            self.handler = None

        def start(self, device_uid, on_pcm):
            self.started = True
            self.handler = on_pcm

        def stop(self):
            self.started = False

    provider = ScriptedTranscriptionProvider()
    provider.prepare()
    capture = Backend()
    service = ASRService(settings=settings, provider=provider, capture_backend=capture)

    service.start_recording()
    capture.handler([0.3] * 100, 0.3, 0.3)
    service.cancel_recording()

    assert service.buffer.count == 0
    assert not service.is_running
    assert provider.received_samples == []
