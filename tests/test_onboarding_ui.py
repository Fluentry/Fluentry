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
