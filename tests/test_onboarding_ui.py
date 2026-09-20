"""The setup wizard, and what a first run exposed.

Every case here came from someone walking through the wizard on a clean
install and reporting it felt wrong: columns that did not line up, a
recommendation on the wrong row, a Continue button that refused without
saying why, and the suspicion that more than one copy was running.
"""

from __future__ import annotations

import pytest

from fluentry.persistence.speech_model import SpeechModel
from fluentry.ui.single_instance import SingleInstance, socket_name


# --- one app per session ----------------------------------------------------


def test_a_second_instance_hands_over_rather_than_starting(qt_app):
    """Two copies both grab the hotkey, the tray and the history.

    The symptom is never an error: it is one keypress starting two
    recordings against one microphone, and nobody suspecting a second copy
    they did not know was running.
    """
    name = f"fluentry-test-{id(qt_app)}"
    first, second = SingleInstance(name), SingleInstance(name)
    try:
        assert first.claim() is True, "the first copy runs"
        assert second.claim() is False, "the second defers to it"
    finally:
        first.release()


def test_the_lock_is_released_so_the_next_launch_can_claim_it(qt_app):
    name = f"fluentry-test-release-{id(qt_app)}"
    first = SingleInstance(name)
    assert first.claim() is True
    first.release()

    second = SingleInstance(name)
    try:
        assert second.claim() is True, "quitting must not lock the app out"
    finally:
        second.release()


def test_a_stale_socket_does_not_lock_the_app_out(qt_app):
    """A crash leaves the socket behind.

    Without clearing it, every later start would decide it was the second
    copy and exit, and the app would never run again.
    """
    from PySide6.QtNetwork import QLocalServer

    name = f"fluentry-test-stale-{id(qt_app)}"
    stale = QLocalServer()
    stale.listen(name)
    stale.close()

    instance = SingleInstance(name)
    try:
        assert instance.claim() is True
    finally:
        instance.release()


def test_the_socket_is_per_user():
    assert socket_name(1000) != socket_name(1001)


# --- the wizard -------------------------------------------------------------


@pytest.fixture
def wizard(qt_app, app_state):
    from fluentry.persistence.settings_types import AccentColorOption, ThemePreference
    from fluentry.ui.onboarding import OnboardingWindow
    from fluentry.ui.theme import palette_for

    return OnboardingWindow(
        app_state, palette_for(ThemePreference.LIGHT, AccentColorOption.BLUE)
    )


def test_the_languages_line_up_in_columns(wizard):
    """A row of separate layouts sizes each item to its own text.

    The columns then drift apart, and a last row with fewer entries spreads
    across the full width instead of lining up with the rows above.
    """
    from PySide6.QtWidgets import QGridLayout

    buttons = wizard._language_buttons.buttons()
    assert len(buttons) > 8, "enough languages to wrap onto a third row"

    from fluentry.ui.onboarding import Step

    assert wizard._pages[Step.LANGUAGE].findChildren(QGridLayout), (
        "a grid is what holds the columns together"
    )
    assert len({b.x() for b in buttons}) <= 4, "every entry sits in one of four columns"


def test_the_recommendation_is_the_engine_the_app_defaults_to(wizard):
    """Not whichever row the sort happened to put first."""
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.VOICE_MODEL
    wizard._show_step()

    labelled = [b for b in wizard._route_buttons.buttons() if "Recommended" in b.text()]
    assert len(labelled) == 1, "exactly one engine is recommended"
    assert SpeechModel.default_model().display_name in labelled[0].text()


def test_the_recommended_engine_is_listed_first(wizard):
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.VOICE_MODEL
    wizard._show_step()
    assert "Recommended" in wizard._route_buttons.buttons()[0].text()


def test_engines_that_cannot_run_are_listed_last(wizard):
    """They used to sit above the one being recommended."""
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.VOICE_MODEL
    wizard._show_step()

    texts = [b.text() for b in wizard._route_buttons.buttons()]
    missing = [i for i, t in enumerate(texts) if "runtime not installed" in t]
    usable = [i for i, t in enumerate(texts) if "runtime not installed" not in t]
    if missing and usable:
        assert min(missing) > max(usable), "unavailable engines go to the bottom"


def test_a_refused_continue_says_what_is_missing(wizard):
    """A greyed-out button and no explanation leaves the user guessing.

    On the engine step what is wanted is a download, which nothing on the
    screen previously said was required at all.
    """
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.VOICE_MODEL
    wizard._show_step()

    if not wizard.continue_button.isEnabled():
        assert "ownload" in wizard.blocked_reason.text(), "say why, not just refuse"


def test_the_playground_has_somewhere_for_the_words_to_land(wizard):
    """It was a button alone on an otherwise empty screen."""
    from fluentry.ui.onboarding import Step
    from fluentry.ui.widgets import Card

    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    assert wizard._pages[Step.PLAYGROUND].findChildren(Card), (
        "the step is grouped rather than loose on the page"
    )
    assert wizard.playground_result.text(), "the result area says what it is for"


def test_a_download_in_the_wizard_shows_that_it_is_working(wizard):
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.VOICE_MODEL
    wizard._show_step()
    assert not wizard.download_progress.isVisibleTo(wizard)

    wizard._download_in_progress = True
    wizard._refresh_model_status()
    assert wizard.download_progress.isVisibleTo(wizard), "silence reads as a freeze"


def test_a_failed_dictation_says_so_instead_of_reverting(wizard):
    """It used to put the placeholder back and look like nothing happened.

    "failed" and "silent" were not in the list of states the step listened
    for, so a dictation that went wrong was indistinguishable from one that
    was never started.
    """
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    wizard._app.last_error = "The model could not be loaded."
    wizard._on_dictation_state("failed")
    assert "could not be loaded" in wizard.playground_result.text()
    assert wizard.playground_result.text() != "Your words will appear here."


def test_a_silent_recording_says_so(wizard):
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    wizard._on_dictation_state("silent")
    assert "silent" in wizard.playground_result.text().lower()


def test_transcribing_does_not_collapse_back_to_the_placeholder(wizard):
    """The reported symptom: "Transcribing…" then the placeholder again."""
    from fluentry.ui.onboarding import Step

    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    wizard._on_dictation_state("transcribing")
    assert wizard.playground_result.text() == "Transcribing…"

    wizard._on_dictation_state("failed")
    assert wizard.playground_result.text() != "Your words will appear here.", (
        "a failure must not be reported as an empty page"
    )


def test_the_wizard_installs_the_runtime_not_just_the_weights(wizard, monkeypatch):
    """The bug: the wizard downloaded weights and left the runtime alone.

    On Ubuntu the system onnxruntime loads the model and transcribes
    nothing, so the weights are useless without a working runtime - yet the
    wizard reached "Try Fluentry" having installed only the weights, and
    every dictation there was refused for want of a runtime.
    """
    from fluentry.services import runtime_installer
    from fluentry.services.runtime_installer import ONNX_ASR

    # A machine whose system runtime has been judged faulty.
    monkeypatch.setattr(runtime_installer, "runtime_for", lambda model: ONNX_ASR)

    captured = {}

    def fake_download_model(model, completion, runtime=None, on_progress=None, on_restart_needed=None):
        captured["runtime"] = runtime

    monkeypatch.setattr(wizard._app, "download_model", fake_download_model)
    wizard._download_model()
    assert captured["runtime"] is ONNX_ASR, (
        "the wizard must install the runtime, not only the weights"
    )


def test_a_download_failure_is_shown_as_an_error(wizard):
    """A runtime that cannot be installed has to say so, visibly.

    It used to be swallowed - the step just sat there - so the user was
    told nothing and assumed the app was broken.
    """
    wizard._on_download_finished("Could not install onnx-asr: no network.")
    assert "no network" in wizard.model_status.text()
    assert wizard.model_status.objectName() == "Error", "shown in the danger colour"

    wizard._download_error = None
    wizard._on_download_finished("")
    assert wizard.model_status.objectName() == "Hint", "cleared once resolved"


def test_the_wizard_restarts_itself_instead_of_asking(wizard, monkeypatch):
    """You asked why the user should restart; they should not have to.

    When a freshly installed runtime needs a fresh process, the wizard
    relaunches the app itself rather than leaving a "please restart" note.
    """
    from fluentry.services import runtime_installer
    from fluentry.services.runtime_installer import ONNX_ASR

    monkeypatch.setattr(runtime_installer, "runtime_for", lambda model: ONNX_ASR)

    restarted = []
    wizard.on_request_restart = lambda: restarted.append(True)

    # download_model calls on_restart_needed when a runtime swap needs a
    # fresh process; the wizard hands it its own signal.
    def fake_download_model(model, completion, runtime=None, on_progress=None, on_restart_needed=None):
        assert runtime is ONNX_ASR, "the runtime is still installed, not skipped"
        on_restart_needed()

    monkeypatch.setattr(wizard._app, "download_model", fake_download_model)
    wizard._download_model()

    # The relaunch is deferred by a short timer so the status line is read.
    import time
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + 3
    while not restarted and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    assert restarted == [True], "the wizard must relaunch the app itself"
    assert "restart" in wizard.model_status.text().lower()


def test_the_playground_asks_for_the_hotkey_when_it_works(wizard, monkeypatch):
    """The button was a second, made-up way to do the one thing dictation is.

    When the global hotkey works, the step asks the user to hold it and
    speak - the real gesture - and hides the fallback button.
    """
    from fluentry.ui.onboarding import Step

    real = wizard._app.readiness_report
    monkeypatch.setattr(
        wizard._app, "readiness_report",
        lambda: [(l, True if l == "Global hotkey" else ok, d) for l, ok, d in real()],
    )
    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    assert not wizard.playground_button.isVisibleTo(wizard), "button hidden when the hotkey works"
    assert "hold" in wizard.playground_hint.text().lower()


def test_the_playground_shows_the_button_when_the_hotkey_cannot_work(wizard, monkeypatch):
    from fluentry.ui.onboarding import Step

    real = wizard._app.readiness_report
    monkeypatch.setattr(
        wizard._app, "readiness_report",
        lambda: [(l, False if l == "Global hotkey" else ok, d) for l, ok, d in real()],
    )
    wizard._flow.step = Step.PLAYGROUND
    wizard._show_step()

    assert wizard.playground_button.isVisibleTo(wizard), "fallback button shown without a hotkey"
