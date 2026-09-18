"""The first-run flow: which step, and when may the user leave it."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fluentry.analytics.database import AnalyticsDatabase
from fluentry.analytics.events import OnboardingOrigin, OnboardingOutcome, OnboardingStep
from fluentry.persistence.defaults import Defaults
from fluentry.persistence.settings_store import SettingsStore
from fluentry.persistence.speech_model import SpeechModel
from fluentry.services.onboarding_flow import LAST_STEP, OnboardingFlow, Readiness, Step


@pytest.fixture
def fresh(settings: SettingsStore) -> SettingsStore:
    settings.bootstrap_onboarding_state(is_true_first_open=True)
    return settings


@pytest.fixture
def flow(fresh: SettingsStore) -> OnboardingFlow:
    return OnboardingFlow(fresh)


def everything_ready() -> Readiness:
    return Readiness(
        has_language_routes=True,
        voice_model_ready=True,
        microphone_ready=True,
        typing_ready=True,
        ai_ready=True,
        playground_ready=True,
    )


# --- when onboarding shows at all -------------------------------------------


def test_an_upgrade_from_before_onboarding_is_left_alone(settings):
    """No stored flag means an existing install, not a new one."""
    assert settings.onboarding_completed is True
    assert settings.should_show_onboarding is False


def test_a_brand_new_install_gets_the_flow(settings):
    settings.bootstrap_onboarding_state(is_true_first_open=True)
    assert settings.should_show_onboarding is True
    assert settings.onboarding_current_step == 0


def test_a_reinstall_over_an_existing_setup_skips_the_flow(settings):
    settings.playground_used = True  # A sign the app has been used before.
    settings.bootstrap_onboarding_state(is_true_first_open=True)
    assert settings.should_show_onboarding is False


def test_a_later_launch_never_re_decides(settings):
    settings.bootstrap_onboarding_state(is_true_first_open=True)
    settings.onboarding_completed = True
    settings.bootstrap_onboarding_state(is_true_first_open=True)
    assert settings.onboarding_completed is True


def test_a_second_launch_that_is_not_the_first_open_skips_the_flow(settings):
    settings.bootstrap_onboarding_state(is_true_first_open=False)
    assert settings.should_show_onboarding is False


@pytest.mark.parametrize(
    "prepare",
    [
        lambda settings: setattr(settings, "playground_used", True),
        lambda settings: setattr(settings, "selected_provider_id", "openai"),
        lambda settings: settings.defaults.set("CustomDictionaryEntries", []),
        lambda settings: settings.defaults.set("HotkeyShortcutKey", {}),
    ],
)
def test_each_legacy_signal_marks_an_existing_install(settings, prepare):
    prepare(settings)
    assert settings.has_legacy_usage_signals() is True


def test_the_default_speech_model_alone_is_not_a_legacy_signal(settings):
    settings.defaults.set("SelectedSpeechModel", SpeechModel.default_model().value)
    assert settings.has_legacy_usage_signals() is False
    settings.defaults.set("SelectedSpeechModel", SpeechModel.WHISPER_LARGE.value)
    assert settings.has_legacy_usage_signals() is True


# --- restarting the flow ----------------------------------------------------


def test_restarting_onboarding_clears_the_progress(fresh):
    fresh.onboarding_current_step = 4
    fresh.onboarding_playground_validated = True
    fresh.onboarding_ai_skipped = True
    fresh.onboarding_selected_language_id = "de"
    fresh.playground_used = True

    fresh.reset_onboarding_progress()

    assert fresh.should_show_onboarding is True
    assert fresh.onboarding_current_step == 0
    assert fresh.onboarding_playground_validated is False
    assert fresh.onboarding_ai_skipped is False
    assert fresh.onboarding_selected_language_id == "en"
    assert fresh.playground_used is False


def test_a_restart_is_reported_as_a_manual_one(fresh):
    assert fresh.analytics_onboarding_origin is OnboardingOrigin.FIRST_RUN
    fresh.reset_onboarding_progress()
    assert fresh.analytics_onboarding_origin is OnboardingOrigin.MANUAL_RESTART


def test_finishing_clears_the_pending_restart(fresh):
    fresh.reset_onboarding_progress()
    fresh.onboarding_completed = True
    assert fresh.analytics_onboarding_origin is OnboardingOrigin.FIRST_RUN


def test_the_1_6_2_forced_reset_is_undone_for_existing_users(settings):
    settings.defaults.set("OnboardingGeneration", 1)
    settings.defaults.set("OnboardingCompleted", False)
    settings.playground_used = True  # An existing install.

    settings.repair_forced_onboarding_reset_if_needed(first_open_at=None)
    assert settings.onboarding_completed is True


def test_the_forced_reset_repair_leaves_a_real_new_install_alone(settings):
    settings.defaults.set("OnboardingGeneration", 1)
    settings.defaults.set("OnboardingCompleted", False)

    settings.repair_forced_onboarding_reset_if_needed(first_open_at=1_800_000_000.0)
    assert settings.onboarding_completed is False


def test_the_forced_reset_repair_respects_a_deliberate_restart(settings):
    settings.defaults.set("OnboardingGeneration", 1)
    settings.playground_used = True
    settings.reset_onboarding_progress()

    settings.repair_forced_onboarding_reset_if_needed(first_open_at=1.0)
    assert settings.onboarding_completed is False


def test_an_old_first_open_counts_as_an_existing_install(settings):
    settings.defaults.set("OnboardingGeneration", 1)
    settings.defaults.set("OnboardingCompleted", False)

    settings.repair_forced_onboarding_reset_if_needed(first_open_at=1_700_000_000.0)
    assert settings.onboarding_completed is True


# --- steps ------------------------------------------------------------------


def test_the_steps_are_in_the_documented_order():
    assert list(Step) == [
        Step.LANDING,
        Step.LANGUAGE,
        Step.VOICE_MODEL,
        Step.PERMISSIONS,
        Step.PLAYGROUND,
        Step.AI_ENHANCEMENT,
    ]
    assert LAST_STEP is Step.AI_ENHANCEMENT


def test_each_step_maps_to_its_analytics_name():
    assert Step.LANDING.analytics_step is OnboardingStep.WELCOME
    assert Step.VOICE_MODEL.analytics_step is OnboardingStep.VOICE_MODEL
    assert Step.AI_ENHANCEMENT.analytics_step is OnboardingStep.AI_ENHANCEMENT


def test_the_primary_button_is_named_for_the_step():
    assert Step.LANDING.primary_button_title == "Next"
    assert Step.LANGUAGE.primary_button_title == "Continue"
    assert Step.AI_ENHANCEMENT.primary_button_title == "Finish Setup"


def test_progress_runs_from_empty_to_full(flow):
    assert flow.progress == 0.0
    flow.step = LAST_STEP
    assert flow.progress == 1.0


def test_compact_progress_counts_the_current_step_as_done(flow):
    assert flow.compact_progress == pytest.approx(1 / 6)
    flow.step = LAST_STEP
    assert flow.compact_progress == 1.0


def test_an_out_of_range_stored_step_lands_on_the_voice_model(fresh):
    fresh.defaults.set("OnboardingCurrentStep", 99)
    # The store clamps, so the flow sees the last step rather than nonsense.
    assert OnboardingFlow(fresh).step is Step.AI_ENHANCEMENT

    fresh.defaults.set("OnboardingCurrentStep", -3)
    assert OnboardingFlow(fresh).step is Step.LANDING


def test_the_step_survives_a_restart(fresh):
    OnboardingFlow(fresh).step = Step.PERMISSIONS
    assert OnboardingFlow(fresh).step is Step.PERMISSIONS


# --- navigation -------------------------------------------------------------


def test_next_and_back_walk_the_steps(flow):
    assert flow.go_next() is Step.LANGUAGE
    assert flow.go_next() is Step.VOICE_MODEL
    assert flow.go_back() is Step.LANGUAGE


def test_back_stops_at_the_first_step(flow):
    assert flow.go_back() is Step.LANDING


def test_next_stops_at_the_last_step(flow):
    flow.step = LAST_STEP
    assert flow.go_next() is LAST_STEP


# --- the gates --------------------------------------------------------------


def test_the_landing_step_never_blocks(flow):
    assert flow.can_continue(Readiness()) is True


def test_the_language_step_needs_an_engine_that_speaks_it(flow):
    flow.step = Step.LANGUAGE
    assert flow.can_continue(Readiness()) is False
    assert flow.can_continue(Readiness(has_language_routes=True)) is True


def test_the_voice_model_step_waits_for_the_download(flow):
    flow.step = Step.VOICE_MODEL
    assert flow.can_continue(Readiness()) is False
    assert flow.can_continue(Readiness(voice_model_ready=True)) is True


def test_a_download_in_progress_blocks_the_voice_model_step(flow):
    flow.step = Step.VOICE_MODEL
    ready = Readiness(voice_model_ready=True, model_preparation_in_progress=True)
    assert flow.can_continue(ready) is False


def test_a_download_elsewhere_does_not_block_another_step(flow):
    flow.step = Step.PERMISSIONS
    ready = Readiness(
        microphone_ready=True, typing_ready=True, model_preparation_in_progress=True
    )
    assert flow.can_continue(ready) is True


def test_permissions_needs_both_a_microphone_and_a_way_to_type(flow):
    flow.step = Step.PERMISSIONS
    assert flow.can_continue(Readiness(microphone_ready=True)) is False
    assert flow.can_continue(Readiness(typing_ready=True)) is False
    assert flow.can_continue(Readiness(microphone_ready=True, typing_ready=True)) is True


def test_the_playground_needs_a_dictation_or_a_skip(flow):
    flow.step = Step.PLAYGROUND
    assert flow.can_continue(Readiness()) is False
    assert flow.can_continue(Readiness(playground_ready=True)) is True


def test_the_playground_will_not_advance_mid_recording(flow):
    flow.step = Step.PLAYGROUND
    assert flow.can_continue(Readiness(playground_ready=True, is_recording=True)) is False


def test_nothing_can_be_skipped_mid_recording(flow):
    assert flow.can_skip(Readiness()) is True
    assert flow.can_skip(Readiness(is_recording=True)) is False
    assert flow.can_skip(Readiness(is_recording_shortcut=True)) is False


def test_the_ai_step_is_ready_once_it_is_configured_or_skipped(flow):
    flow.step = Step.AI_ENHANCEMENT
    assert flow.can_continue(Readiness()) is False
    assert flow.can_continue(Readiness(ai_ready=True)) is True


# --- finishing --------------------------------------------------------------


def test_finishing_marks_onboarding_done(flow, fresh):
    flow.step = Step.AI_ENHANCEMENT
    flow.finish()
    assert fresh.onboarding_completed is True


def test_skipping_ai_also_finishes_and_records_the_skip(flow, fresh):
    flow.step = Step.AI_ENHANCEMENT
    flow.skip_ai_enhancement()
    assert fresh.onboarding_ai_skipped is True
    assert fresh.onboarding_completed is True


def test_skipping_the_playground_moves_on_without_a_dictation(flow, fresh):
    flow.step = Step.PLAYGROUND
    assert flow.skip_playground() is Step.AI_ENHANCEMENT
    assert fresh.onboarding_playground_skipped is True
    assert flow.can_continue(Readiness(playground_ready=True)) is False  # AI not ready yet.


def test_a_real_dictation_satisfies_the_playground(flow, fresh):
    flow.step = Step.PLAYGROUND
    flow.mark_playground_validated()
    assert fresh.onboarding_playground_validated is True
    assert fresh.playground_used is True
    assert flow.can_continue(Readiness(playground_ready=True)) is True


# --- analytics --------------------------------------------------------------


DISTANT_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def analytics(tmp_path):
    database = AnalyticsDatabase(
        path=tmp_path / "analytics.sqlite3",
        distinct_id="test-install-id",
        app_version="test",
        zone=timezone.utc,
        first_weekday=0,
    )
    yield database
    database.close()


def recorded(analytics, name: str) -> list[dict]:
    import json

    events = []
    for item in analytics.ready_outbox(limit=100, moment=DISTANT_FUTURE):
        payload = json.loads(item.payload.decode("utf-8"))
        if payload["event"] == name:
            events.append(payload["properties"])
    return events


def test_each_step_completion_is_recorded_once(fresh, analytics):
    flow = OnboardingFlow(fresh, analytics=analytics)
    flow.go_next()
    flow.go_back()
    flow.go_next()

    welcome = [
        properties
        for properties in recorded(analytics, "onboarding_step_completed")
        if properties["step"] == "welcome"
    ]
    assert len(welcome) == 1
    assert welcome[0]["outcome"] == OnboardingOutcome.CONTINUED.value


def test_finishing_is_reported_as_completing_the_flow(fresh, analytics):
    flow = OnboardingFlow(fresh, analytics=analytics)
    flow.step = Step.AI_ENHANCEMENT
    flow.finish()

    completed = recorded(analytics, "onboarding_step_completed")
    assert completed[-1]["outcome"] == OnboardingOutcome.COMPLETED.value
    # Completing the flow is its own event, not a property of the step.
    assert len(recorded(analytics, "onboarding_completed")) == 1


def test_a_broken_analytics_database_never_blocks_setup(fresh):
    class Exploding:
        def record_onboarding_step_completed(self, *args, **kwargs):
            raise RuntimeError("disk is on fire")

        def record_onboarding_step_viewed(self, *args, **kwargs):
            raise RuntimeError("disk is on fire")

    flow = OnboardingFlow(fresh, analytics=Exploding())
    flow.record_step_viewed()
    assert flow.go_next() is Step.LANGUAGE
