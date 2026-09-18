"""Port of AnalyticsDatabaseTests."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from fluentry.analytics.database import (
    AnalyticsDatabase,
    DetailedAnalyticsConsentGate,
    tryout_duration_bucket,
)
from fluentry.analytics.events import (
    ActivityKind,
    AnalyticsEvent,
    ModelDescriptor,
    ModelDownloadOutcome,
    ModelDownloadSource,
    OnboardingOrigin,
    OnboardingStep,
    TryoutFailureStage,
    TryoutOutcome,
    TryoutStartMethod,
    UsageMode,
    automatically_collects_dictation_performance,
    dictation_summary_line,
)
from fluentry.analytics.system_configuration import AnalyticsSystemConfiguration

TEST_CONFIGURATION = AnalyticsSystemConfiguration(
    ram_gb=24, chip="AMD Ryzen 9 7950X", os_version="Ubuntu 25.04"
)

DISTANT_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def at(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


@pytest.fixture
def make_database(tmp_path):
    created = []

    def factory(name: str = "analytics.sqlite3", configuration=TEST_CONFIGURATION):
        database = AnalyticsDatabase(
            path=tmp_path / name,
            distinct_id="test-install-id",
            app_version="test",
            system_configuration=configuration,
            zone=timezone.utc,
            first_weekday=0,
        )
        created.append(database)
        return database

    yield factory
    for database in created:
        database.close()


def events(database: AnalyticsDatabase, moment: datetime = DISTANT_FUTURE):
    result = []
    for item in database.ready_outbox(limit=100, moment=moment):
        payload = json.loads(item.payload.decode("utf-8"))
        result.append((payload["event"], payload["properties"]))
    return result


# --- policy -----------------------------------------------------------------


def test_beta_performance_collection_uses_the_installed_version():
    assert automatically_collects_dictation_performance("1.6.10-beta.1")
    assert automatically_collects_dictation_performance("2.0-BETA")
    assert not automatically_collects_dictation_performance("1.6.10")
    assert not automatically_collects_dictation_performance("unknown")


def test_dictation_summary_is_one_stable_line_and_names_the_slowest_stage():
    line = dictation_summary_line(
        asr_milliseconds=52, ai_milliseconds=150, ready_milliseconds=225, outcome="success"
    )
    assert line == (
        "DICTATION_SUMMARY asrMs=52 aiMs=150 appOverheadMs=23 readyMs=225 "
        "slowest=ai outcome=success"
    )
    assert "\n" not in line


def test_dictation_summary_names_asr_and_app_overhead_too():
    assert "slowest=asr" in dictation_summary_line(200, 10, 220, "success")
    assert "slowest=app_overhead" in dictation_summary_line(10, 10, 500, "success")


def test_detailed_consent_gate_rejects_work_queued_before_opt_out():
    gate = DetailedAnalyticsConsentGate()
    queued = gate.current_generation
    current = gate.advance()

    assert not gate.accepts(queued)
    assert gate.accepts(current)


# --- aggregation ------------------------------------------------------------


def test_activity_is_deduplicated_and_usage_is_aggregated_by_day(make_database):
    database = make_database()
    first_day = at(1_735_689_600)  # 2025-01-01 UTC
    second_day = first_day + timedelta(days=1)
    descriptor = ModelDescriptor(provider="Fluid Audio", model="Parakeet TDT")

    database.record_activity(ActivityKind.APP, first_day)
    database.record_activity(ActivityKind.APP, first_day + timedelta(seconds=60))
    database.record_usage(UsageMode.DICTATION, descriptor, None, first_day)
    database.record_usage(UsageMode.DICTATION, descriptor, None, first_day)
    database.finalize_days(before=second_day)

    recorded = events(database)
    assert sum(1 for name, _ in recorded if name == AnalyticsEvent.ACTIVE_USER.value) == 1

    usage = next(p for name, p in recorded if name == AnalyticsEvent.USAGE_DAILY_SUMMARY.value)
    assert usage["dictation_count"] == 2
    assert usage["command_count"] == 0
    assert usage["ram_gb"] == 24
    assert usage["chip"] == "AMD Ryzen 9 7950X"
    assert usage["$set"] == {"ram_gb": 24, "chip": "AMD Ryzen 9 7950X"}
    assert usage["platform"] == "linux"
    assert usage["$os"] == "Linux"

    model = next(
        p for name, p in recorded if name == AnalyticsEvent.MODEL_USAGE_DAILY_SUMMARY.value
    )
    assert model["provider"] == "fluid_audio"
    assert model["model"] == "parakeet_tdt"
    assert model["use_count"] == 2


def test_raw_usage_never_leaves_before_the_day_ends(make_database):
    database = make_database()
    first_day = at(1_735_689_600)
    database.record_usage(UsageMode.DICTATION, None, None, first_day)

    same_day = events(database, moment=first_day + timedelta(hours=1))
    assert all(name == AnalyticsEvent.ACTIVE_USER.value for name, _ in same_day)

    database.finalize_days(before=first_day + timedelta(days=1))
    assert any(
        name == AnalyticsEvent.USAGE_DAILY_SUMMARY.value for name, _ in events(database)
    )


# --- performance histograms -------------------------------------------------


def test_beta_performance_is_aggregated_into_one_daily_distribution(make_database):
    database = make_database()
    first_day = at(1_735_689_600)
    second_day = first_day + timedelta(days=1)

    database.record_dictation_performance(20, 100, "1.6.10-beta.1", first_day)
    database.record_dictation_performance(60, 900, "1.6.10-beta.1", first_day + timedelta(seconds=60))
    database.record_dictation_performance(1600, None, "1.6.10-beta.1", first_day + timedelta(seconds=120))
    database.finalize_days(before=second_day)

    summaries = [
        p
        for name, p in events(database)
        if name == AnalyticsEvent.DICTATION_PERFORMANCE_DAILY_SUMMARY.value
    ]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["measured_app_version"] == "1.6.10-beta.1"
    assert summary["measured_os_version"] == "Ubuntu 25.04"
    assert summary["asr_sample_count"] == 3
    assert summary["asr_p50_bucket"] == "75"
    assert summary["asr_p95_bucket"] == "2000"
    assert summary["fluid_intelligence_sample_count"] == 2
    assert summary["fluid_intelligence_p50_bucket"] == "100"
    assert summary["fluid_intelligence_p95_bucket"] == "1000"
    # Raw values and any content must never appear.
    for forbidden in (
        "asr_average_ms",
        "asr_max_ms",
        "fluid_intelligence_average_ms",
        "transcript",
        "window_title",
    ):
        assert forbidden not in summary


def test_detailed_opt_out_preserves_the_automatic_performance_summary(make_database):
    database = make_database()
    first_day = at(1_735_689_600)
    second_day = first_day + timedelta(days=1)

    database.record_dictation_performance(75, 200, "1.6.10-beta.1", first_day)
    database.purge_detailed_analytics()
    database.finalize_days(before=second_day)

    assert [name for name, _ in events(database)] == [
        AnalyticsEvent.DICTATION_PERFORMANCE_DAILY_SUMMARY.value
    ]


def test_performance_summary_keeps_the_os_version_from_measurement_time(make_database, tmp_path):
    first_day = at(1_735_689_600)
    second_day = first_day + timedelta(days=1)

    original = make_database("analytics-os.sqlite3", TEST_CONFIGURATION)
    original.record_dictation_performance(75, 200, "1.6.10-beta.1", first_day)
    original.close()

    upgraded = make_database(
        "analytics-os.sqlite3",
        AnalyticsSystemConfiguration(ram_gb=24, chip="AMD Ryzen 9 7950X", os_version="Ubuntu 25.10"),
    )
    upgraded.finalize_days(before=second_day)
    summary = next(
        p
        for name, p in events(upgraded)
        if name == AnalyticsEvent.DICTATION_PERFORMANCE_DAILY_SUMMARY.value
    )
    assert summary["measured_os_version"] == "Ubuntu 25.04"


def test_performance_recording_is_a_no_op_without_measurements(make_database):
    database = make_database()
    first_day = at(1_735_689_600)
    database.record_dictation_performance(None, None, "1.6.10-beta.1", first_day)
    database.finalize_days(before=first_day + timedelta(days=1))
    assert events(database) == []


# --- outbox -----------------------------------------------------------------


def test_acknowledgement_purges_the_outbox(make_database):
    database = make_database()
    flush_date = datetime.now(timezone.utc)
    database.record_activity(ActivityKind.APP, flush_date - timedelta(days=8))

    items = database.ready_outbox(limit=50, moment=flush_date)
    assert len(items) == 1

    database.acknowledge_uploaded([item.id for item in items], flush_date)
    assert database.ready_outbox(limit=50, moment=flush_date) == []


def test_daily_activity_waits_for_the_week_end_while_detailed_events_stay_ready(make_database):
    database = make_database()
    monday = at(1_736_121_600)  # 2025-01-06 UTC, a Monday
    next_monday = monday + timedelta(days=7)

    database.record_onboarding_started(OnboardingOrigin.FIRST_RUN, monday)
    for day_offset in range(1, 7):
        database.record_activity(ActivityKind.APP, monday + timedelta(days=day_offset))

    during_week = events(database, moment=next_monday - timedelta(seconds=1))
    assert [name for name, _ in during_week] == [AnalyticsEvent.ONBOARDING_STARTED.value]

    after_week = events(database, moment=next_monday)
    assert sum(1 for name, _ in after_week if name == AnalyticsEvent.ACTIVE_USER.value) == 7
    assert sum(1 for name, _ in after_week if name == AnalyticsEvent.ONBOARDING_STARTED.value) == 1


def test_retry_backs_off_without_losing_the_event(make_database):
    database = make_database()
    moment = at(1_735_689_600)
    database.record_onboarding_started(OnboardingOrigin.FIRST_RUN, moment)
    items = database.ready_outbox(limit=10, moment=moment)
    assert items

    database.retry([item.id for item in items], moment)
    assert database.ready_outbox(limit=10, moment=moment) == []
    # It comes back once the backoff elapses.
    assert database.ready_outbox(limit=10, moment=moment + timedelta(hours=2))


# --- model downloads --------------------------------------------------------


def test_interrupted_download_is_recovered_without_a_duration(make_database):
    descriptor = ModelDescriptor(provider="whisper", model="large")
    download_id = "abc123"

    original = make_database("downloads.sqlite3")
    original.record_model_download_started(
        download_id, descriptor, ModelDownloadSource.SETTINGS, at(100)
    )
    original.close()

    recovered = make_database("downloads.sqlite3")
    recovered.recover_interrupted_model_downloads(at(200))
    finish = next(
        p for name, p in events(recovered) if name == AnalyticsEvent.MODEL_DOWNLOAD_FINISHED.value
    )
    assert finish["outcome"] == "interrupted"
    assert "duration_seconds" not in finish


def test_download_finish_before_start_still_produces_one_complete_pair(make_database):
    database = make_database()
    descriptor = ModelDescriptor(provider="whisper", model="large")
    download_id = "def456"
    finished_at = at(200)

    database.record_model_download_finished(
        download_id,
        descriptor,
        ModelDownloadSource.SETTINGS,
        ModelDownloadOutcome.SUCCEEDED,
        duration=25,
        moment=finished_at,
    )
    database.record_model_download_started(
        download_id, descriptor, ModelDownloadSource.SETTINGS, finished_at + timedelta(seconds=1)
    )

    recorded = events(database)
    starts = [p for name, p in recorded if name == AnalyticsEvent.MODEL_DOWNLOAD_STARTED.value]
    finishes = [p for name, p in recorded if name == AnalyticsEvent.MODEL_DOWNLOAD_FINISHED.value]
    assert len(starts) == 1
    assert len(finishes) == 1
    assert finishes[0]["duration_seconds"] == 25


# --- onboarding tryout ------------------------------------------------------


def test_onboarding_tryout_terminal_event_aggregates_attempts_and_dimensions(make_database):
    database = make_database()
    entered_at = at(100)

    database.record_onboarding_step_viewed(
        OnboardingStep.PLAYGROUND, OnboardingOrigin.FIRST_RUN, entered_at
    )
    database.record_onboarding_tryout_attempt_started(
        TryoutStartMethod.HOTKEY, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=5)
    )
    database.record_onboarding_tryout_attempt_result(
        TryoutOutcome.EMPTY, None, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=20)
    )
    database.record_onboarding_tryout_attempt_started(
        TryoutStartMethod.BUTTON, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=35)
    )
    database.finish_onboarding_tryout(
        TryoutOutcome.SUCCESS,
        TryoutFailureStage.POST_PROCESSING,
        OnboardingOrigin.FIRST_RUN,
        entered_at + timedelta(seconds=75),
    )
    database.finish_onboarding_tryout(
        TryoutOutcome.ERROR,
        TryoutFailureStage.TRANSCRIPTION,
        OnboardingOrigin.FIRST_RUN,
        entered_at + timedelta(seconds=80),
    )

    tryouts = [
        p for name, p in events(database) if name == AnalyticsEvent.ONBOARDING_TRYOUT_FINISHED.value
    ]
    assert len(tryouts) == 1, "only the first terminal event is reported"
    tryout = tryouts[0]
    assert tryout["outcome"] == "success"
    assert tryout["attempt_count_bucket"] == "2"
    assert tryout["duration_bucket"] == "30s_plus"
    assert tryout["start_method"] == "button"
    assert tryout["failure_stage"] == "post_processing"
    assert tryout["ram_gb"] == 24


def test_onboarding_tryout_skip_before_attempt_omits_attempt_properties(make_database):
    database = make_database()
    entered_at = at(100)

    database.record_onboarding_step_viewed(
        OnboardingStep.PLAYGROUND, OnboardingOrigin.FIRST_RUN, entered_at
    )
    database.skip_onboarding_tryout(OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=10))

    tryout = next(
        p for name, p in events(database) if name == AnalyticsEvent.ONBOARDING_TRYOUT_FINISHED.value
    )
    assert tryout["outcome"] == "skipped_before_attempt"
    assert tryout["duration_bucket"] == "10s"
    assert "attempt_count_bucket" not in tryout
    assert "start_method" not in tryout


@pytest.mark.parametrize(
    "duration,bucket",
    [
        (0, "500ms"), (0.5, "500ms"), (0.501, "1s"), (1, "1s"), (1.001, "1_5s"),
        (1.5, "1_5s"), (1.501, "2s"), (5.2, "5_5s"), (10, "10s"), (20.01, "20_5s"),
        (29.9, "30s"), (30, "30s_plus"), (75, "30s_plus"),
    ],
)
def test_onboarding_tryout_duration_uses_granular_sub_thirty_second_buckets(duration, bucket):
    assert tryout_duration_bucket(duration) == bucket


def test_onboarding_tryout_skip_after_failure_retains_attempt_context(make_database):
    database = make_database()
    entered_at = at(100)

    database.record_onboarding_step_viewed(
        OnboardingStep.PLAYGROUND, OnboardingOrigin.FIRST_RUN, entered_at
    )
    database.record_onboarding_tryout_attempt_started(
        TryoutStartMethod.HOTKEY, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=5)
    )
    database.record_onboarding_tryout_attempt_result(
        TryoutOutcome.ERROR,
        TryoutFailureStage.TRANSCRIPTION,
        OnboardingOrigin.FIRST_RUN,
        entered_at + timedelta(seconds=15),
    )
    database.skip_onboarding_tryout(OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=40))

    tryout = next(
        p for name, p in events(database) if name == AnalyticsEvent.ONBOARDING_TRYOUT_FINISHED.value
    )
    assert tryout["outcome"] == "skipped_after_attempt"
    assert tryout["attempt_count_bucket"] == "1"
    assert tryout["duration_bucket"] == "30s_plus"
    assert tryout["start_method"] == "hotkey"
    assert tryout["failure_stage"] == "transcription"


def test_onboarding_tryout_attempt_state_survives_a_database_reopen(make_database):
    entered_at = at(100)

    original = make_database("tryout.sqlite3")
    original.record_onboarding_step_viewed(
        OnboardingStep.PLAYGROUND, OnboardingOrigin.FIRST_RUN, entered_at
    )
    original.record_onboarding_tryout_attempt_started(
        TryoutStartMethod.HOTKEY, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=5)
    )
    original.record_onboarding_tryout_attempt_result(
        TryoutOutcome.EMPTY, None, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=15)
    )
    original.close()

    reopened = make_database("tryout.sqlite3")
    reopened.record_onboarding_tryout_attempt_started(
        TryoutStartMethod.HOTKEY, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=30)
    )
    reopened.finish_onboarding_tryout(
        TryoutOutcome.SUCCESS, None, OnboardingOrigin.FIRST_RUN, entered_at + timedelta(seconds=65)
    )

    tryout = next(
        p for name, p in events(reopened) if name == AnalyticsEvent.ONBOARDING_TRYOUT_FINISHED.value
    )
    assert tryout["attempt_count_bucket"] == "2"
    assert tryout["duration_bucket"] == "30s_plus"


# --- purge ------------------------------------------------------------------


def test_purging_detailed_analytics_preserves_only_daily_activity(make_database):
    database = make_database()
    first_day = at(1_735_689_600)
    second_day = first_day + timedelta(days=1)
    descriptor = ModelDescriptor(provider="Fluid Audio", model="Parakeet TDT")

    database.record_activity(ActivityKind.APP, first_day)
    database.record_usage(UsageMode.DICTATION, descriptor, None, first_day)
    database.record_onboarding_started(OnboardingOrigin.FIRST_RUN, first_day)
    database.record_onboarding_step_viewed(
        OnboardingStep.WELCOME, OnboardingOrigin.FIRST_RUN, first_day
    )
    database.record_model_download_started(
        "download-1", descriptor, ModelDownloadSource.ONBOARDING, first_day
    )
    database.finalize_days(before=second_day)

    database.purge_detailed_analytics()
    assert [name for name, _ in events(database)] == [AnalyticsEvent.ACTIVE_USER.value]

    database.record_activity(ActivityKind.CORE_ACTION, first_day + timedelta(seconds=60))
    database.finalize_days(before=second_day + timedelta(days=1))
    assert [name for name, _ in events(database)] == [AnalyticsEvent.ACTIVE_USER.value]
