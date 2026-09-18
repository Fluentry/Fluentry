"""The whole setup flow, driven through the real UI.

This clicks the actual widgets — the language buttons, the engine list, the
record button, Continue, Skip — on a real `AppState`, with the Qt event loop
running, and asserts what a person would see at each moment.

Only the hardware is replaced: the microphone plays the test fixture, the
target application is a `RecordingBackend`, and the transcriber is scripted
so its timing can be controlled. Everything else is shipping code.

`test_the_whole_setup_flow_with_a_real_model` runs the same walkthrough with
real Parakeet weights; it is marked `model` and deselected by default.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the GUI extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from fluentry.app import AppState  # noqa: E402
from fluentry.persistence.history_store import TranscriptionHistoryStore  # noqa: E402
from fluentry.persistence.settings_types import (  # noqa: E402
    AccentColorOption,
    ThemePreference,
)
from fluentry.persistence.speech_model import SpeechModel  # noqa: E402
from fluentry.platform.text_injection import RecordingBackend  # noqa: E402
from fluentry.services.onboarding_flow import Step  # noqa: E402
from fluentry.services.providers.base import (  # noqa: E402
    ScriptedTranscriptionProvider,
    TranscriptionResult,
)
from fluentry.services.providers.whisper import read_wav_as_mono_float  # noqa: E402
from fluentry.ui.onboarding import OnboardingWindow  # noqa: E402
from fluentry.ui.theme import palette_for  # noqa: E402

FIXTURE = Path(__file__).parent / "resources" / "dictation_fixture.wav"
TIMEOUT_SECONDS = 60.0


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication(["fluentry-ui-tests"])


class FixtureMicrophone:
    """A microphone that plays the test fixture when asked."""

    def __init__(self) -> None:
        self.samples = read_wav_as_mono_float(FIXTURE)
        self._on_pcm = None

    def start(self, device_uid, on_pcm) -> None:
        self._on_pcm = on_pcm

    def stop(self) -> None:
        self._on_pcm = None

    @property
    def is_running(self) -> bool:
        return self._on_pcm is not None

    def speak(self) -> None:
        assert self._on_pcm is not None, "the app never opened the microphone"
        for index in range(0, len(self.samples), 1600):
            block = self.samples[index : index + 1600]
            peak = max((abs(value) for value in block), default=0.0)
            self._on_pcm(block, peak * 0.7, peak)


class SlowProvider(ScriptedTranscriptionProvider):
    """A transcriber that takes a moment, like a real model does.

    An instant one hides every intermediate state the user actually sees.
    """

    delay_seconds: float = 0.35

    def transcribe(self, samples, language=None) -> TranscriptionResult:
        time.sleep(self.delay_seconds)
        return super().transcribe(samples, language=language)


class UI:
    """The onboarding window plus the pumping a headless test needs."""

    def __init__(self, state: AppState, window: OnboardingWindow) -> None:
        self.state = state
        self.window = window
        self.seen_results: list[str] = []

    def pump(self, seconds: float = 0.05) -> None:
        """Run the event loop, sampling what the result label shows."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QApplication.processEvents()
            label = self.window.playground_result.text()
            if not self.seen_results or self.seen_results[-1] != label:
                self.seen_results.append(label)
            time.sleep(0.01)

    def pump_until(self, predicate, what: str) -> None:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self.pump(0.02)
            if predicate():
                return
        raise AssertionError(f"timed out waiting for {what}")

    @property
    def result(self) -> str:
        return self.window.playground_result.text()

    @property
    def button(self) -> str:
        return self.window.playground_button.text()

    def dictate(self) -> None:
        """Record, speak, stop, and wait for the transcript to land."""
        self.window.playground_button.click()
        self.pump_until(lambda: self.state.asr.is_running, "recording to start")
        self.state.capture.speak()
        self.pump(0.05)
        self.window.playground_button.click()
        self.pump_until(lambda: bool(self.state.asr.final_text), "the transcript")
        self.pump(0.05)


def build_ui(settings, tmp_path, provider, qt_app) -> UI:
    state = AppState(
        settings=settings,
        history=TranscriptionHistoryStore(load=False),
        start_services=False,
    )
    state.provider = state.asr.provider = provider
    state.capture = state.asr.capture_backend = FixtureMicrophone()
    state.typing.backend = RecordingBackend()
    state.settings.bootstrap_onboarding_state(is_true_first_open=True)

    window = OnboardingWindow(
        state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE, qt_app)
    )
    # Actually show it: widget visibility is meaningless on a hidden parent,
    # and the point here is to assert what a person would see.
    window.show()
    QApplication.processEvents()
    return UI(state, window)


@pytest.fixture
def ui(settings, tmp_path, qt_app) -> UI:
    return build_ui(
        settings,
        tmp_path,
        SlowProvider(responses=["hello fluid voice", "a second dictation"]),
        qt_app,
    )


# --- the playground's visible states ----------------------------------------


def test_recording_then_transcribing_then_the_result(ui):
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    assert ui.result == ""
    assert ui.button == "Start Recording"

    ui.window.playground_button.click()
    ui.pump(0.05)
    assert ui.button == "Stop Recording"
    assert ui.result == "Listening…"

    ui.state.capture.speak()
    ui.window.playground_button.click()
    ui.pump(0.05)
    # The model is still working: say so rather than going blank.
    assert ui.result == "Transcribing…"
    assert ui.button == "Start Recording"

    ui.pump_until(lambda: "hello fluid voice" in ui.result, "the transcript")
    assert ui.result == "“hello fluid voice”"


def test_the_label_never_goes_blank_mid_dictation(ui):
    """The reported flicker: text appearing, vanishing, then "Listening…"."""
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    ui.seen_results.clear()

    ui.dictate()

    # Once something has been shown, nothing may blank it out again.
    after_first = ui.seen_results.index("Listening…")
    assert "" not in ui.seen_results[after_first:], ui.seen_results


def test_a_second_dictation_never_shows_the_first_result(ui):
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    ui.dictate()
    assert ui.result == "“hello fluid voice”"

    ui.window.playground_button.click()
    ui.pump(0.05)
    # The old transcript is stale the instant a new recording starts.
    assert ui.result == "Listening…"
    assert "hello fluid voice" not in ui.result

    ui.state.capture.speak()
    ui.window.playground_button.click()
    ui.pump_until(lambda: "second" in ui.result, "the second transcript")
    assert ui.result == "“a second dictation”"


def test_the_button_label_flips_immediately_on_click(ui):
    """It must not wait for a worker thread to catch up."""
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()

    ui.window.playground_button.click()
    assert ui.button == "Stop Recording"  # No pumping: same call stack.

    ui.pump(0.05)
    ui.state.capture.speak()
    ui.window.playground_button.click()
    assert ui.button == "Start Recording"


def test_the_transcript_reaches_the_target_application(ui):
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    ui.dictate()
    assert ui.state.typing.backend.typed == ["hello fluid voice"]


# --- the whole walkthrough --------------------------------------------------


def walk_through_setup(ui: UI) -> None:
    """Click through every step the way a person would."""
    window = ui.window

    # 1. Landing.
    assert window._flow.step is Step.LANDING
    assert window.continue_button.isEnabled() is True
    window.continue_button.click()
    ui.pump()

    # 2. Language.
    assert window._flow.step is Step.LANGUAGE
    english = next(
        button
        for button in window._language_buttons.buttons()
        if button.property("languageID") == "en"
    )
    english.click()
    ui.pump()
    assert window.continue_button.isEnabled() is True
    window.continue_button.click()
    ui.pump()

    # 3. Voice engine: the model is already loaded, so this is ready.
    assert window._flow.step is Step.VOICE_MODEL
    ui.pump_until(window.continue_button.isEnabled, "the engine to be ready")
    window.continue_button.click()
    ui.pump()

    # 4. Permissions.
    assert window._flow.step is Step.PERMISSIONS
    window.continue_button.click()
    ui.pump()

    # 5. Playground: a real dictation is what unlocks Continue.
    assert window._flow.step is Step.PLAYGROUND
    assert window.continue_button.isEnabled() is False
    ui.dictate()
    assert window.continue_button.isEnabled() is True
    window.continue_button.click()
    ui.pump()

    # 6. AI enhancement is optional, so Skip finishes setup.
    assert window._flow.step is Step.AI_ENHANCEMENT
    assert window.skip_button.isVisible() is True
    window.skip_button.click()
    ui.pump()


def test_setup_can_be_completed_end_to_end(ui, monkeypatch):
    monkeypatch.setattr(ui.state, "model_is_ready", lambda model: True)
    finished = []
    ui.window.finished_onboarding.connect(lambda: finished.append(True))

    walk_through_setup(ui)

    assert finished == [True]
    assert ui.state.settings.onboarding_completed is True
    assert ui.state.typing.backend.typed == ["hello fluid voice"]


def test_every_step_is_reachable_and_titled(ui, monkeypatch):
    monkeypatch.setattr(ui.state, "model_is_ready", lambda model: True)
    seen = []
    for step in Step:
        ui.window._flow.step = step
        ui.window._show_step()
        ui.pump(0.02)
        seen.append((ui.window.title.text(), ui.window.continue_button.text()))
    assert [title for title, _ in seen] == [step.title for step in Step]


def test_going_back_does_not_lose_the_finished_playground(ui, monkeypatch):
    monkeypatch.setattr(ui.state, "model_is_ready", lambda model: True)
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    ui.dictate()
    assert ui.window.continue_button.isEnabled() is True

    ui.window.back_button.click()
    ui.pump()
    assert ui.window._flow.step is Step.PERMISSIONS

    ui.window.continue_button.click()
    ui.pump()
    assert ui.window._flow.step is Step.PLAYGROUND
    # Already validated, so the user is not made to dictate twice.
    assert ui.window.continue_button.isEnabled() is True


def test_the_playground_can_be_skipped_when_dictation_will_not_work(ui):
    ui.window._flow.step = Step.PLAYGROUND
    ui.window._show_step()
    assert ui.window.continue_button.isEnabled() is False
    assert ui.window.skip_button.isEnabled() is True

    ui.window.skip_button.click()
    ui.pump()
    assert ui.window._flow.step is Step.AI_ENHANCEMENT
    assert ui.state.settings.onboarding_playground_skipped is True


# --- the real model ---------------------------------------------------------


@pytest.mark.model
def test_the_whole_setup_flow_with_a_real_model(settings, tmp_path, qt_app, monkeypatch):
    """The same walkthrough, with real Parakeet weights doing the listening."""
    from fluentry.services.providers.onnx_asr import OnnxAsrProvider

    if not OnnxAsrProvider.is_available():
        pytest.skip("onnx-asr is not installed")
    cache = Path.home() / ".cache" / "fluentry" / "models" / "onnx"
    provider = OnnxAsrProvider(model=SpeechModel.PARAKEET_TDT, cache_root=cache)
    if not provider.is_downloaded:
        pytest.skip("Parakeet is not downloaded; run the app to fetch it")
    provider.prepare()

    ui = build_ui(settings, tmp_path, provider, qt_app)
    monkeypatch.setattr(ui.state, "model_is_ready", lambda model: True)
    finished = []
    ui.window.finished_onboarding.connect(lambda: finished.append(True))

    walk_through_setup(ui)

    assert finished == [True]
    assert ui.state.settings.onboarding_completed is True
    typed = ui.state.typing.backend.typed
    assert len(typed) == 1
    assert "hello" in typed[0].lower() and "fluid" in typed[0].lower()
    assert "" not in ui.seen_results[ui.seen_results.index("Listening…") :]
