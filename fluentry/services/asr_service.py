"""The dictation pipeline: capture, transcribe, format, deliver.

`ASRService`, reduced to the part that is actually the app's behaviour rather
in one place. It owns the recording lifecycle and drives:

    microphone → buffer → silence gate → provider → text pipeline
                                                  → optional AI enhancement
                                                  → typing / clipboard
                                                  → history

Everything it depends on is injected, so a whole dictation can be run in a
test with a scripted provider and a recording keyboard backend.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..persistence.history_entry import TranscriptionHistoryEntry
from ..persistence.settings_store import SettingsStore
from ..persistence.settings_types import TextInsertionMode
from .audio_runtime import ThreadSafeAudioBuffer
from .providers.base import TranscriptionProviderError, TranscriptionResult
from .text_pipeline import PipelineContext, PipelineResult, TextPipeline

TARGET_SAMPLE_RATE = 16_000

#: The silence gate only applies to clips this short; a longer recording is
#: always worth transcribing.
MAXIMUM_SILENCE_GATE_SECONDS = 4

#: Calibrated conservatively against real captures. All three must hold, which
#: keeps quiet speech and short words on the ASR path.
SILENCE_PEAK_THRESHOLD = 0.01
SILENCE_RMS_THRESHOLD = 0.002
SILENCE_FRAME_RMS_THRESHOLD = 0.0045


@dataclass(frozen=True)
class ShortAudioSilenceAssessment:
    duration_milliseconds: int
    is_eligible: bool
    should_skip_transcription: bool
    peak_amplitude: float
    rms_amplitude: float
    maximum_frame_rms: float


def should_assess_short_audio_silence(
    is_enabled: bool, use_dictionary_training_path: bool, has_recognized_streaming_preview: bool
) -> bool:
    return is_enabled and not use_dictionary_training_path and not has_recognized_streaming_preview


def assess_short_audio_silence(
    samples: Sequence[float], sample_rate: int = TARGET_SAMPLE_RATE
) -> ShortAudioSilenceAssessment:
    """Decide whether a short clip is silent enough to skip transcribing."""
    count = len(samples)
    duration_milliseconds = (
        int(round(count / sample_rate * 1000)) if sample_rate > 0 else 0
    )
    maximum_sample_count = max(sample_rate, 0) * MAXIMUM_SILENCE_GATE_SECONDS

    if not count or sample_rate <= 0 or count > maximum_sample_count:
        return ShortAudioSilenceAssessment(
            duration_milliseconds=duration_milliseconds,
            is_eligible=False,
            should_skip_transcription=False,
            peak_amplitude=0.0,
            rms_amplitude=0.0,
            maximum_frame_rms=0.0,
        )

    frame_size = max(sample_rate // 50, 1)  # 20 ms
    peak = 0.0
    total_square_sum = 0.0
    frame_square_sum = 0.0
    frame_sample_count = 0
    maximum_frame_rms = 0.0

    for sample in samples:
        if not math.isfinite(sample):
            # Fail open: a corrupt buffer must never silently drop a dictation.
            return ShortAudioSilenceAssessment(
                duration_milliseconds=duration_milliseconds,
                is_eligible=True,
                should_skip_transcription=False,
                peak_amplitude=peak,
                rms_amplitude=0.0,
                maximum_frame_rms=maximum_frame_rms,
            )
        magnitude = abs(sample)
        peak = max(peak, magnitude)
        square = float(sample) * float(sample)
        total_square_sum += square
        frame_square_sum += square
        frame_sample_count += 1
        if frame_sample_count == frame_size:
            maximum_frame_rms = max(maximum_frame_rms, math.sqrt(frame_square_sum / frame_sample_count))
            frame_square_sum = 0.0
            frame_sample_count = 0

    if frame_sample_count > 0:
        maximum_frame_rms = max(maximum_frame_rms, math.sqrt(frame_square_sum / frame_sample_count))
    rms_amplitude = math.sqrt(total_square_sum / count)

    should_skip = (
        peak < SILENCE_PEAK_THRESHOLD
        and rms_amplitude < SILENCE_RMS_THRESHOLD
        and maximum_frame_rms < SILENCE_FRAME_RMS_THRESHOLD
    )
    return ShortAudioSilenceAssessment(
        duration_milliseconds=duration_milliseconds,
        is_eligible=True,
        should_skip_transcription=should_skip,
        peak_amplitude=peak,
        rms_amplitude=rms_amplitude,
        maximum_frame_rms=maximum_frame_rms,
    )


@dataclass
class DictationOutcome:
    raw_text: str = ""
    final_text: str = ""
    was_skipped_as_silent: bool = False
    was_ai_processed: bool = False
    ai_processing_error: str | None = None
    transcription_duration_milliseconds: int = 0
    ai_processing_duration_milliseconds: int | None = None
    did_send: bool = False
    entry: TranscriptionHistoryEntry | None = None
    error: str | None = None


class ASRService:
    def __init__(
        self,
        settings: SettingsStore,
        provider=None,
        capture_backend=None,
        pipeline: TextPipeline | None = None,
        typing_service=None,
        clipboard=None,
        history_store=None,
        enhance: Callable[[str, PipelineContext], str] | None = None,
        focus_context: Callable[[], PipelineContext] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.capture_backend = capture_backend
        self.pipeline = pipeline or TextPipeline(settings)
        self.typing_service = typing_service
        self.clipboard = clipboard
        self.history_store = history_store
        self._enhance = enhance
        self._focus_context = focus_context or (lambda: PipelineContext())
        self._clock = clock

        self.buffer = ThreadSafeAudioBuffer()
        self.is_running = False
        self.final_text = ""
        self.partial_transcription = ""
        self.last_level = 0.0
        self._lock = threading.RLock()
        self._observers: list[Callable[[str], None]] = []

    # --- observation ------------------------------------------------------

    def add_state_observer(self, callback: Callable[[str], None]) -> Callable[[], None]:
        self._observers.append(callback)

        def remove() -> None:
            if callback in self._observers:
                self._observers.remove(callback)

        return remove

    def _notify(self, state: str) -> None:
        for observer in list(self._observers):
            try:
                observer(state)
            except Exception:
                pass

    # --- recording --------------------------------------------------------

    def start_recording(self, device_uid: str | None = None) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.buffer.clear()
            self.partial_transcription = ""
            # The previous transcript is no longer "the result": anything
            # showing it must not keep doing so through a new recording.
            self.final_text = ""
            self.is_running = True

        if self.capture_backend is not None:
            try:
                self.capture_backend.start(device_uid, self._on_pcm)
            except Exception:
                with self._lock:
                    self.is_running = False
                self._notify("failed")
                return False

        self._notify("recording")
        return True

    def _on_pcm(self, samples: list[float], rms_value: float, peak_value: float) -> None:
        self.buffer.append(samples)
        self.last_level = rms_value

    def cancel_recording(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            self.is_running = False
        if self.capture_backend is not None:
            self.capture_backend.stop()
        self.buffer.clear()
        self._notify("cancelled")

    def stop_recording_and_transcribe(self) -> DictationOutcome:
        with self._lock:
            if not self.is_running:
                return DictationOutcome(error="Not recording.")
            self.is_running = False

        if self.capture_backend is not None:
            self.capture_backend.stop()
        samples = self.buffer.get_all()
        self.buffer.clear()
        self._notify("transcribing")
        return self.process_samples(samples)

    # --- transcription ----------------------------------------------------

    def process_samples(self, samples: Sequence[float]) -> DictationOutcome:
        outcome = DictationOutcome()

        if should_assess_short_audio_silence(
            is_enabled=self.settings.skip_silent_recordings_enabled,
            use_dictionary_training_path=False,
            has_recognized_streaming_preview=bool(self.partial_transcription),
        ):
            assessment = assess_short_audio_silence(samples)
            if assessment.is_eligible and assessment.should_skip_transcription:
                outcome.was_skipped_as_silent = True
                self._notify("idle")
                return outcome

        if self.provider is None:
            outcome.error = "No speech model is selected."
            self._notify("failed")
            return outcome

        try:
            if not self.provider.is_ready:
                self.provider.prepare()
            language = self.settings.selected_whisper_language_code
            result: TranscriptionResult = self.provider.transcribe(samples, language=language)
        except TranscriptionProviderError as error:
            outcome.error = str(error)
            self._notify("failed")
            return outcome
        except Exception as error:
            outcome.error = str(error)
            self._notify("failed")
            return outcome

        outcome.raw_text = result.text
        outcome.transcription_duration_milliseconds = result.duration_milliseconds
        return self.deliver(outcome)

    # --- delivery ---------------------------------------------------------

    def deliver(self, outcome: DictationOutcome) -> DictationOutcome:
        context = self._focus_context()
        self.pipeline.refresh_dictionary()
        cleaned = self.pipeline.clean(outcome.raw_text, context)

        enhanced: str | None = None
        wants_ai = self.settings.ai_enhancement_enabled_for_app(context.bundle_id)
        if wants_ai and self._enhance is not None and cleaned.strip():
            started = self._clock()
            try:
                enhanced = self._enhance(cleaned, context)
                outcome.was_ai_processed = True
            except Exception as error:
                # The transcription is still good; fall back to it rather than
                # losing the user's words to a provider outage.
                outcome.ai_processing_error = str(error)
                enhanced = None
            outcome.ai_processing_duration_milliseconds = int((self._clock() - started) * 1000)

        result: PipelineResult = self.pipeline.run(
            outcome.raw_text, context=context, enhanced_text=enhanced
        )
        outcome.final_text = result.final_text
        outcome.did_send = result.should_send
        self.final_text = result.final_text

        if not result.final_text.strip() and not result.should_send:
            self._notify("idle")
            return outcome

        self._insert(result.final_text, result.should_send)
        outcome.entry = self._record_history(outcome, context)
        self._notify("idle")
        return outcome

    def _insert(self, text: str, should_send: bool) -> None:
        mode = self.settings.text_insertion_mode
        # In clipboard-only mode the copy *is* the delivery, so it happens
        # whether or not the "also copy" preference is on — otherwise the
        # user would be left with nothing at all.
        wants_copy = self.settings.copy_transcription_to_clipboard
        if (wants_copy or mode is TextInsertionMode.CLIPBOARD_ONLY) and self.clipboard is not None:
            self.clipboard.write_text(text)

        if self.typing_service is None or not mode.inserts_into_the_focused_app:
            return
        if text:
            self.typing_service.type_text(text, mode=mode)
        if should_send:
            key = self.settings.spoken_send_key
            self.typing_service.send_key("Return", key.modifier_flags)

    def _record_history(
        self, outcome: DictationOutcome, context: PipelineContext
    ) -> TranscriptionHistoryEntry | None:
        if self.history_store is None or not self.settings.save_transcription_history:
            return None
        return self.history_store.add_entry(
            raw_text=outcome.raw_text,
            processed_text=outcome.final_text,
            app_name=context.app_name or "",
            window_title=context.window_title or "",
            was_ai_processed=outcome.was_ai_processed,
            processing_model=self.settings.selected_model if outcome.was_ai_processed else None,
            transcription_duration_milliseconds=outcome.transcription_duration_milliseconds,
            ai_processing_duration_milliseconds=outcome.ai_processing_duration_milliseconds,
            ai_processing_error=outcome.ai_processing_error,
        )

    # --- paste last -------------------------------------------------------

    def paste_last_transcription(self) -> bool:
        if self.history_store is None or self.typing_service is None:
            return False
        text = self.history_store.latest_clipboard_text
        if not text:
            return False
        return self.typing_service.type_text(text, mode=TextInsertionMode.RELIABLE_PASTE)
