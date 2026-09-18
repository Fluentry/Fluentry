"""One dictation, from keypress to typed text.

These assemble the real `AppState` — the real hotkey manager, buffer,
pipeline, typing service, history database and analytics database — and
replace only the three things that are genuinely hardware:

* the keyboard, by `SyntheticHotkeyBackend`
* the microphone, by a capture backend that plays the test fixture
* the target application, by `RecordingBackend`, which keeps what was typed

Everything between them is the shipping code. A test that stubbed the
pipeline or the history store would pass while the app was broken, which is
the failure these exist to catch.

The final case swaps the scripted transcriber for real Whisper Tiny; it is
marked `model` and deselected by default because it needs weights on disk.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fluentry.app import AppState
from fluentry.persistence.history_database import TranscriptionHistoryWriter
from fluentry.persistence.history_store import TranscriptionHistoryStore
from fluentry.persistence.settings_store import HotkeyActivationMode
from fluentry.persistence.settings_types import (
    CustomDictionaryEntry,
    TextInsertionMode,
)
from fluentry.models.hotkey import HotkeyShortcut
from fluentry.models.keycodes import KEY_ESC, KEY_RIGHTALT, ModifierFlags
from fluentry.platform.clipboard import InMemoryClipboard
from fluentry.platform.hotkey_listener import SyntheticHotkeyBackend
from fluentry.platform.text_injection import RecordingBackend
from fluentry.services.audio_device import AudioDeviceInfo, TransportType
from fluentry.services.microphone_coordinator import (
    MicrophonePreferenceCoordinator,
    StaticDeviceManager,
)
from fluentry.services.providers.base import ScriptedTranscriptionProvider
from fluentry.services.providers.whisper import read_wav_as_mono_float
from fluentry.services.text_pipeline import PipelineContext

FIXTURE = Path(__file__).parent / "resources" / "dictation_fixture.wav"

#: The model cache is deliberately the user's real one, not the per-test
#: sandbox, so the weights are downloaded at most once on this machine.
MODEL_CACHE = Path.home() / ".cache" / "fluentry" / "models" / "whisper"

#: Long enough that a wedged dictation fails the test instead of hanging CI.
IDLE_TIMEOUT_SECONDS = 60.0

MICROPHONE = AudioDeviceInfo(
    id=1,
    uid="alsa_input.usb-ProFX",
    name="ProFX Microphone",
    has_input=True,
    transport_type=TransportType.USB,
)


class FixtureCaptureBackend:
    """A microphone that plays a WAV file.

    `start` only arms it; the test pushes the audio explicitly, so "the user
    spoke" is a visible step rather than a race with a background thread.
    """

    def __init__(self, path: Path = FIXTURE, chunk_seconds: float = 0.1) -> None:
        self.samples = read_wav_as_mono_float(path)
        self.chunk = max(1, int(chunk_seconds * 16_000))
        self.started_devices: list[str | None] = []
        self.stops = 0
        self._on_pcm = None

    @property
    def is_running(self) -> bool:
        return self._on_pcm is not None

    def start(self, device_uid: str | None, on_pcm) -> None:
        self.started_devices.append(device_uid)
        self._on_pcm = on_pcm

    def stop(self) -> None:
        self.stops += 1
        self._on_pcm = None

    def speak(self, samples=None) -> None:
        """Deliver audio the way a real backend does: in small chunks."""
        assert self._on_pcm is not None, "the app never opened the microphone"
        samples = self.samples if samples is None else samples
        for index in range(0, len(samples), self.chunk):
            block = samples[index : index + self.chunk]
            peak = max((abs(value) for value in block), default=0.0)
            rms = (sum(value * value for value in block) / max(1, len(block))) ** 0.5
            self._on_pcm(block, rms, peak)


class Harness:
    """An assembled app plus the handles a test needs to drive it."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.keyboard: SyntheticHotkeyBackend = state.hotkeys.backend
        self.microphone: FixtureCaptureBackend = state.capture
        self.target: RecordingBackend = state.typing.backend
        self.states: list[str] = []
        self.notices: list[tuple[str, str]] = []
        self._idle = threading.Event()

        state.add_state_observer(self._record_state)
        state.add_notice_observer(lambda title, body: self.notices.append((title, body)))

    def _record_state(self, state: str) -> None:
        self.states.append(state)
        if state == "idle":
            self._idle.set()

    # --- driving ----------------------------------------------------------

    def press_shortcut(self) -> None:
        self._idle.clear()
        self.keyboard.press_key(KEY_RIGHTALT)

    def release_shortcut(self) -> None:
        self.keyboard.release_key(KEY_RIGHTALT)

    def dictate(self, samples=None) -> None:
        """A whole dictation: press, speak, release, wait for the result."""
        self.press_shortcut()
        self.release_shortcut()
        assert self.state.asr.is_running, "the shortcut did not start recording"
        self.microphone.speak(samples)
        self.press_shortcut()
        self.release_shortcut()
        self.wait_for_idle()

    def wait_for_idle(self) -> None:
        assert self._idle.wait(IDLE_TIMEOUT_SECONDS), "the dictation never finished"

    @property
    def typed(self) -> list[str]:
        return list(self.target.typed)


def build_app(settings, tmp_path, provider, devices=(MICROPHONE,), mode=None) -> Harness:
    """The real app, with the hardware boundaries replaced."""
    writer = TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    history = TranscriptionHistoryStore(writer=writer)
    history.wait_until_loaded()

    # Tap once to start, tap again to stop: the least timing-dependent mode.
    settings.hotkey_mode = mode or HotkeyActivationMode.TOGGLE
    settings.primary_dictation_shortcuts = [
        HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])
    ]
    settings.cancel_recording_hotkey_shortcut = HotkeyShortcut.keyboard(
        KEY_ESC, ModifierFlags.NONE
    )

    state = AppState(settings=settings, history=history, start_services=False)
    state.provider = state.asr.provider = provider
    state.capture = state.asr.capture_backend = FixtureCaptureBackend()
    state.typing.backend = RecordingBackend()
    state.typing.clipboard = state.clipboard = state.asr.clipboard = InMemoryClipboard()
    state.typing.paste_settle_seconds = 0
    state.hotkeys.backend = SyntheticHotkeyBackend()
    state.devices = StaticDeviceManager(inputs=list(devices), default_input=devices[0] if devices else None)
    state.microphones = MicrophonePreferenceCoordinator(
        settings=settings,
        devices=state.devices,
        present_notice=state._present_microphone_notice,
    )
    # No live window manager in a test, so the focused app is fixed.
    state.asr._focus_context = lambda: PipelineContext(
        app_name="Text Editor", bundle_id="org.gnome.texteditor", window_title="notes.md"
    )

    state.apply_shortcuts()
    state.hotkeys.start()

    harness = Harness(state)
    harness.writer = writer
    return harness


@pytest.fixture
def app(settings, tmp_path):
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["hello fluid boys"]),
    )
    yield harness
    harness.state.hotkeys.stop()
    harness.writer.shutdown()


# --- the whole round trip ---------------------------------------------------


def test_a_shortcut_press_records_speaks_and_types(app):
    app.dictate()

    assert app.typed == ["hello fluid boys"]
    assert app.state.asr.is_running is False


def test_the_overlay_is_told_to_appear_and_disappear(app):
    app.dictate()
    assert app.states == ["recording", "transcribing", "idle"]


def test_the_configured_microphone_is_the_one_opened(app):
    app.dictate()
    assert app.microphone.started_devices == [MICROPHONE.uid]
    assert app.microphone.stops == 1
    assert app.microphone.is_running is False


def test_the_audio_the_microphone_produced_is_what_reaches_the_model(app):
    app.dictate()
    provider = app.state.provider
    assert len(provider.received_samples) == len(app.microphone.samples)
    assert provider.received_samples[:10] == app.microphone.samples[:10]


def test_the_dictation_is_written_to_history(app):
    app.dictate()
    app.state.history.finish_pending_writes()

    entry = app.state.history.entries[0]
    assert entry.raw_text == "hello fluid boys"
    assert entry.processed_text == "hello fluid boys"
    assert entry.app_name == "Text Editor"
    assert entry.window_title == "notes.md"
    assert entry.character_count == len("hello fluid boys")


def test_history_survives_a_restart(app, settings, tmp_path):
    app.dictate()
    app.state.history.finish_pending_writes()
    app.writer.shutdown()

    reopened = TranscriptionHistoryStore(
        writer=TranscriptionHistoryWriter(path=tmp_path / "history.sqlite3")
    )
    reopened.wait_until_loaded()
    assert [entry.raw_text for entry in reopened.entries] == ["hello fluid boys"]
    reopened.writer.shutdown()


# --- holding the shortcut ---------------------------------------------------


def test_holding_the_shortcut_records_and_releasing_it_stops(settings, tmp_path):
    """Push-to-talk: the key is held for the whole utterance."""
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["held the key"]),
        mode=HotkeyActivationMode.AUTOMATIC,
    )
    try:
        now = [1000.0]
        harness.state.hotkeys._clock = lambda: now[0]

        harness.press_shortcut()
        assert harness.state.asr.is_running, "holding the shortcut did not start recording"

        harness.microphone.speak()
        now[0] += 1.5  # Held well past the tap threshold.
        harness.release_shortcut()
        harness.wait_for_idle()

        assert harness.typed == ["held the key"]
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_a_quick_tap_in_automatic_mode_still_toggles(settings, tmp_path):
    """A tap is a toggle, so the user need not hold for a long dictation."""
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["tapped instead"]),
        mode=HotkeyActivationMode.AUTOMATIC,
    )
    try:
        now = [1000.0]
        harness.state.hotkeys._clock = lambda: now[0]

        harness.press_shortcut()
        now[0] += 0.05  # Well under the tap threshold.
        harness.release_shortcut()
        assert harness.state.asr.is_running, "a tap should leave it recording"

        harness.microphone.speak()
        now[0] += 2.0
        harness.press_shortcut()
        now[0] += 0.05
        harness.release_shortcut()
        harness.wait_for_idle()

        assert harness.typed == ["tapped instead"]
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_hold_mode_stops_the_moment_the_key_goes_up(settings, tmp_path):
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["push to talk"]),
        mode=HotkeyActivationMode.HOLD,
    )
    try:
        harness.press_shortcut()
        assert harness.state.asr.is_running
        harness.microphone.speak()
        harness.release_shortcut()
        harness.wait_for_idle()

        assert harness.typed == ["push to talk"]
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


# --- the pipeline is really in the path -------------------------------------


def test_the_custom_dictionary_corrects_what_gets_typed(app):
    app.state.settings.custom_dictionary_entries = [
        CustomDictionaryEntry(triggers=["fluid boys"], replacement="Fluentry")
    ]
    app.state.invalidate_dictionary_cache()

    app.dictate()

    assert app.typed == ["hello Fluentry"]
    app.state.history.finish_pending_writes()
    entry = app.state.history.entries[0]
    # History keeps both: what was heard, and what was inserted.
    assert entry.raw_text == "hello fluid boys"
    assert entry.processed_text == "hello Fluentry"


def test_spoken_punctuation_reaches_the_typed_text(settings, tmp_path):
    settings.auto_convert_punctuation_enabled = True
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["hello literal comma world"]),
    )
    try:
        harness.dictate()
        assert harness.typed == ["hello, world"]
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_filler_words_are_dropped_before_typing(settings, tmp_path):
    settings.remove_filler_words_enabled = True
    harness = build_app(
        settings,
        tmp_path,
        ScriptedTranscriptionProvider(responses=["um hello there uh world"]),
    )
    try:
        harness.dictate()
        assert harness.typed == ["hello there world"]
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_the_transcript_is_copied_to_the_clipboard_when_asked(app):
    app.state.settings.copy_transcription_to_clipboard = True
    app.dictate()
    assert app.state.clipboard.read_text() == "hello fluid boys"


def test_paste_mode_inserts_through_the_clipboard_and_restores_it(app):
    app.state.clipboard.write_text("something the user had copied")
    # The insertion mode is read from settings on every insert, not captured
    # when the typing service was built.
    app.state.settings.text_insertion_mode = TextInsertionMode.RELIABLE_PASTE

    app.dictate()

    assert app.target.chords, "no paste chord was sent"
    assert app.state.clipboard.read_text() == "something the user had copied"


# --- clipboard-only delivery (upstream PR #484) -----------------------------


def test_clipboard_only_copies_without_typing_anywhere(app):
    """The mode that always works on Wayland: no window is ever touched."""
    app.state.settings.text_insertion_mode = TextInsertionMode.CLIPBOARD_ONLY

    app.dictate()

    assert app.typed == []
    assert app.target.chords == []
    assert app.state.clipboard.read_text() == "hello fluid boys"


def test_clipboard_only_copies_even_when_the_backup_copy_is_off(app):
    """The copy is the delivery here, so the user is never left with nothing."""
    app.state.settings.copy_transcription_to_clipboard = False
    app.state.settings.text_insertion_mode = TextInsertionMode.CLIPBOARD_ONLY

    app.dictate()

    assert app.state.clipboard.read_text() == "hello fluid boys"


@pytest.mark.parametrize(
    "mode", [TextInsertionMode.STANDARD, TextInsertionMode.RELIABLE_PASTE]
)
def test_the_other_modes_still_reach_the_application(app, mode):
    app.state.settings.text_insertion_mode = mode
    app.dictate()
    assert app.typed or app.target.chords, f"{mode} delivered nothing"


def test_clipboard_only_still_records_history(app):
    app.state.settings.text_insertion_mode = TextInsertionMode.CLIPBOARD_ONLY
    app.dictate()
    app.state.history.finish_pending_writes()
    assert app.state.history.entries[0].processed_text == "hello fluid boys"


# --- cancelling and failing -------------------------------------------------


def test_the_cancel_shortcut_throws_the_recording_away(app):
    app.press_shortcut()
    app.release_shortcut()
    app.microphone.speak()

    app.keyboard.press_key(KEY_ESC)
    app.keyboard.release_key(KEY_ESC)

    assert app.state.asr.is_running is False
    assert app.typed == []
    assert app.microphone.stops == 1
    assert app.states == ["recording", "idle"]


def test_a_failing_model_reports_instead_of_typing_nothing(settings, tmp_path):
    provider = ScriptedTranscriptionProvider()
    provider.failure = RuntimeError("the model fell over")
    harness = build_app(settings, tmp_path, provider)
    try:
        harness.dictate()
        assert harness.typed == []
        assert any("Dictation failed" == title for title, _body in harness.notices)
        assert harness.state.last_error is not None
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_silence_is_skipped_without_typing_anything(settings, tmp_path):
    settings.skip_silent_recordings_enabled = True
    harness = build_app(
        settings, tmp_path, ScriptedTranscriptionProvider(responses=["should never be typed"])
    )
    try:
        harness.dictate(samples=[0.0] * 8_000)
        assert harness.typed == []
        assert harness.state.provider.received_samples == []
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_a_second_dictation_starts_from_a_clean_buffer(app):
    app.state.provider.responses = ["first one", "second one"]

    app.dictate()
    app.dictate()

    assert app.typed == ["first one", "second one"]
    # The second pass must see only the second recording's audio.
    assert len(app.state.provider.received_samples) == 2 * len(app.microphone.samples)


# --- analytics --------------------------------------------------------------


def analytics_events(database) -> list[dict]:
    """Whatever is queued for sending, decoded.

    Usage is aggregated per day, so the day has to be closed before the
    summary appears — which is exactly what the uploader does.
    """
    import json
    from datetime import datetime, timezone

    tomorrow = datetime(2100, 1, 1, tzinfo=timezone.utc)
    database.finalize_days(before=tomorrow)
    return [
        json.loads(item.payload.decode("utf-8"))
        for item in database.ready_outbox(limit=50, moment=tomorrow)
    ]


def test_a_completed_dictation_is_counted(settings, tmp_path):
    settings.share_detailed_analytics = True
    harness = build_app(
        settings, tmp_path, ScriptedTranscriptionProvider(responses=["hello there"])
    )
    harness.state._start_analytics()
    assert harness.state.analytics is not None, "analytics did not open"
    try:
        harness.dictate()

        events = analytics_events(harness.state.analytics)
        summaries = [event for event in events if event["event"] == "usage_daily_summary"]
        assert len(summaries) == 1
        assert summaries[0]["properties"]["dictation_count"] == 1

        models = [event for event in events if event["event"] == "model_usage_daily_summary"]
        assert any(
            event["properties"]["model"] == settings.selected_speech_model.value
            for event in models
        )
        # Nothing about what was said may leave the machine.
        assert "hello there" not in str(events)
    finally:
        harness.state.analytics.close()
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


def test_nothing_is_recorded_when_analytics_are_off(settings, tmp_path):
    from fluentry.persistence.defaults import data_home

    settings.share_detailed_analytics = False
    harness = build_app(
        settings, tmp_path, ScriptedTranscriptionProvider(responses=["hello there"])
    )
    harness.state._start_analytics()
    try:
        harness.dictate()
        assert harness.state.analytics is None
        assert not (data_home() / "analytics.sqlite3").exists()
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()


# --- the real model ---------------------------------------------------------


@pytest.mark.model
def test_a_real_dictation_with_whisper_tiny(settings, tmp_path):
    """The same round trip, with real weights over the real fixture."""
    from fluentry.persistence.speech_model import SpeechModel
    from fluentry.services.providers.whisper import FasterWhisperProvider

    if not FasterWhisperProvider.is_available():
        pytest.skip("faster-whisper is not installed")
    provider = FasterWhisperProvider(model=SpeechModel.WHISPER_TINY, download_root=MODEL_CACHE)
    try:
        provider.prepare()
    except Exception as error:  # offline, or no weights cached yet
        pytest.skip(f"Whisper Tiny is unavailable: {error}")

    settings.custom_dictionary_entries = [
        CustomDictionaryEntry(triggers=["fluid boys", "fluid voice"], replacement="Fluentry")
    ]
    harness = build_app(settings, tmp_path, provider)
    try:
        harness.dictate()

        assert len(harness.typed) == 1
        typed = harness.typed[0]
        assert "hello" in typed.lower()
        # The fixture is misheard as "fluid boys"; the dictionary fixes it.
        assert "Fluentry" in typed

        harness.state.history.finish_pending_writes()
        entry = harness.state.history.entries[0]
        assert entry.processed_text == typed
        assert entry.transcription_duration_milliseconds >= 0
    finally:
        harness.state.hotkeys.stop()
        harness.writer.shutdown()
