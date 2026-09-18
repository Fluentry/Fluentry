"""Port of StatsSnapshotTests, HistoryPresentationTests and SettingsNavigationStateTests."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fluentry.persistence.history_entry import (
    DictationAudioMetadata,
    TranscriptionHistoryEntry,
)
from fluentry.services.history_presentation import (
    HistoryTextDiff,
    scan_audio_availability,
)
from fluentry.services.stats_snapshot import StatsSnapshot
from fluentry.ui.navigation import SettingsNavigationState, SettingsSection, SidebarItem

ZONE = ZoneInfo("America/Los_Angeles")


def at(day: int, month: int = 9, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=ZONE)


def entry(day: int, text: str, app: str = "Notes", ai: bool = False, month: int = 9):
    return TranscriptionHistoryEntry(
        timestamp=at(day, month=month),
        raw_text="raw must not count",
        processed_text=text,
        app_name=app,
        window_title="",
        was_ai_processed=ai,
    )


@pytest.fixture
def entries():
    # 2026-09-07 is a Monday; the 5th and 6th are the weekend.
    return [
        entry(7, "one  two\nthree", ai=True),
        entry(7, "four", app=""),
        entry(6, "five six"),
        entry(4, "seven"),
        entry(3, "eight"),
    ]


# --- stats ------------------------------------------------------------------


def test_totals_records_and_chart_bounds(entries):
    original = list(entries)
    snapshot = StatsSnapshot.build(entries, now=at(7), zone=ZONE)

    assert entries == original, "building a summary must not mutate history"
    assert snapshot.total_words == 8
    assert snapshot.total_transcriptions == 5
    assert snapshot.average_words_per_transcription == 1
    assert snapshot.ai_enhancement_rate == 20
    assert snapshot.longest_transcription_words == 3
    assert snapshot.most_words_in_day == 4
    assert snapshot.most_transcriptions_in_day == 2
    assert len(snapshot.daily_word_counts(7)) == 7
    assert len(snapshot.activity) == 30
    assert snapshot.activity[-1][1] == 4
    assert sum(words for _, words in snapshot.activity) == 8
    assert snapshot.top_apps_formatted(3) == ["Notes", "Unknown"]


def test_streaks_and_the_weekday_projection(entries):
    snapshot = StatsSnapshot.build(entries, now=at(7), zone=ZONE)

    assert snapshot.current_streak == 2
    assert snapshot.best_streak == 2

    weekday = snapshot.using_weekdays(True)
    assert weekday.current_streak == 3
    assert weekday.best_streak == 3
    assert snapshot.current_streak == 2, "the projection must not alter the cached summary"


def test_a_stale_summary_reports_no_current_streak(entries):
    stale = StatsSnapshot.build(entries, now=at(10), zone=ZONE)
    assert stale.current_streak == 0
    assert stale.weekday_current_streak == 0
    assert stale.best_streak == 2


def test_weekend_activity_does_not_break_a_weekday_streak():
    weekend = StatsSnapshot.build(
        [entry(3, "x"), entry(4, "x"), entry(6, "x")], now=at(6), zone=ZONE
    )
    assert weekend.weekday_current_streak == 2
    assert weekend.weekday_best_streak == 2


def test_streaks_use_calendar_days_across_a_dst_transition():
    # 2026-03-08 is the US spring-forward date; that day is only 23 hours long.
    dst = StatsSnapshot.build(
        [entry(7, "a", month=3), entry(8, "b", month=3), entry(9, "c", month=3)],
        now=at(9, month=3),
        zone=ZONE,
    )
    assert dst.current_streak == 3
    assert dst.best_streak == 3


def test_an_empty_history_produces_an_empty_summary():
    empty = StatsSnapshot.build([], now=at(7), zone=ZONE)
    assert empty.total_words == 0
    assert empty.current_streak == 0
    assert empty.best_streak == 0
    assert empty.peak_hour_formatted == "N/A"
    assert empty.total_milestones_achieved == 0
    assert empty.total_milestones_possible == 18


def test_time_saved_is_conservative():
    snapshot = StatsSnapshot.build([entry(7, "one two three")], now=at(7), zone=ZONE)
    assert snapshot.formatted_time_saved(typing_wpm=0) == "< 1m"
    assert snapshot.formatted_time_saved(typing_wpm=200) == "< 1m"

    big = StatsSnapshot(total_words=6_000)
    assert big.formatted_time_saved(typing_wpm=40) == "1h 50m"
    assert StatsSnapshot(total_words=3_000).formatted_time_saved(typing_wpm=40) == "55m"


def test_peak_hour_is_reported_as_a_range():
    snapshot = StatsSnapshot.build(
        [
            TranscriptionHistoryEntry(
                timestamp=at(7, hour=14),
                raw_text="",
                processed_text="hello",
                app_name="Notes",
                window_title="",
            )
        ],
        now=at(7),
        zone=ZONE,
    )
    assert snapshot.peak_hour_formatted == "2 PM-3 PM"


def test_milestones_track_words_transcriptions_and_streaks():
    snapshot = StatsSnapshot(total_words=10_000, total_transcriptions=100, best_streak=14)
    assert [label for _, achieved, label in snapshot.word_milestones if achieved] == ["1K", "10K"]
    assert [label for _, achieved, label in snapshot.transcription_milestones if achieved] == [
        "50",
        "100",
    ]
    assert [label for _, achieved, label in snapshot.streak_milestones if achieved] == [
        "7 days",
        "14 days",
    ]
    assert snapshot.total_milestones_achieved == 6


def test_a_large_history_still_summarizes():
    entries = [entry(7, "word " * 100)] * 9_000
    snapshot = StatsSnapshot.build(entries, now=at(7), zone=ZONE)
    assert snapshot.total_words == 900_000
    assert snapshot.longest_transcription_words == 100
    assert len(snapshot.activity) == 30


# --- history presentation ---------------------------------------------------


def test_audio_availability_reports_only_files_still_on_disk(tmp_path):
    saved = tmp_path / "saved.wav"
    saved.write_bytes(b"\x00")

    def exists(name: str) -> bool:
        return (tmp_path / name).exists()

    assert scan_audio_availability(["saved.wav", "missing.wav", "saved.wav"], exists) == {
        "saved.wav"
    }

    saved.unlink()
    assert scan_audio_availability(["saved.wav"], exists) == set()


def test_no_metadata_means_no_file_queries():
    def exists(_name: str) -> bool:
        raise AssertionError("no metadata must mean no file queries")

    assert scan_audio_availability([], exists) == set()


def test_clipboard_text_prefers_processed_then_raw_then_nothing():
    audio = DictationAudioMetadata("saved.wav", 10_300, 331_000, 16_000, 1, None)
    enhanced = TranscriptionHistoryEntry(
        raw_text=" raw words ",
        processed_text=" final words ",
        app_name="Notes",
        window_title="",
        was_ai_processed=True,
        processing_model="historical-model",
        transcription_duration_milliseconds=57,
        ai_processing_duration_milliseconds=104,
        ai_tokens_per_second=746,
        audio=audio,
    )
    assert enhanced.clipboard_text == "final words"

    raw_only = TranscriptionHistoryEntry(
        raw_text=" raw words ", processed_text="  ", app_name="Notes", window_title=""
    )
    assert raw_only.clipboard_text == "raw words"

    empty = TranscriptionHistoryEntry(
        raw_text=" ", processed_text=" ", app_name="Notes", window_title=""
    )
    assert empty.clipboard_text is None


def test_history_entry_round_trips_audio_and_timings():
    audio = DictationAudioMetadata("saved.wav", 10_300, 331_000, 16_000, 1, None)
    enhanced = TranscriptionHistoryEntry(
        raw_text="raw",
        processed_text="final",
        app_name="Notes",
        window_title="",
        was_ai_processed=True,
        processing_model="historical-model",
        transcription_duration_milliseconds=57,
        ai_processing_duration_milliseconds=104,
        ai_tokens_per_second=746,
        audio=audio,
    )
    decoded = TranscriptionHistoryEntry.from_dict(enhanced.to_dict())

    assert decoded == enhanced
    assert decoded.processing_model == "historical-model"
    assert decoded.audio == audio


def test_older_history_entry_without_processing_times_still_decodes():
    payload = {
        "id": "ID",
        "timestamp": "2026-01-01T00:00:00Z",
        "rawText": "raw",
        "processedText": "final",
        "appName": "Notes",
        "windowTitle": "",
        "characterCount": 5,
        "wasAIProcessed": False,
    }
    decoded = TranscriptionHistoryEntry.from_dict(payload)
    assert decoded.transcription_duration_milliseconds is None
    assert decoded.ai_tokens_per_second is None
    assert decoded.audio is None


def test_metric_labels():
    assert TranscriptionHistoryEntry.formatted_tokens_per_second(746, compact=True) == "746 tok/s"
    assert TranscriptionHistoryEntry.formatted_tokens_per_second(12.34) == "12.3 tokens/sec"
    assert TranscriptionHistoryEntry.formatted_duration(104) == "104 ms"
    assert TranscriptionHistoryEntry.formatted_duration(1_500) == "1.5 s"


def test_preview_text_is_bounded():
    entry = TranscriptionHistoryEntry(
        raw_text="", processed_text="  " + "x" * 200, app_name="", window_title=""
    )
    assert len(entry.preview_text) == 80
    assert entry.preview_text.endswith("...")


@pytest.mark.parametrize(
    "original,final",
    [
        ("Hello world", "Hello world"),
        ("Hello world", "Hello, world!"),
        ("red blue", "green blue"),
        ("", "new words"),
        ("old words", ""),
        ("one  two\nthree", "One two\n\nthree."),
        ("hello hello world", "hello world"),
        ("வணக்கம் 👋🏽 café", "வணக்கம் 👋🏽 Café!"),
    ],
)
def test_diff_preserves_every_character_and_reports_change(original, final):
    diff = HistoryTextDiff.compare(original=original, final=final)
    assert diff is not None
    assert "".join(run.text for run in diff.original) == original
    assert "".join(run.text for run in diff.final) == final
    assert diff.has_changes == (original != final)


def test_diff_marks_the_replaced_words():
    diff = HistoryTextDiff.compare(original="red blue", final="green blue")
    assert "".join(run.text for run in diff.original if run.changed) == "red"
    assert "".join(run.text for run in diff.final if run.changed) == "green"


def test_diff_is_bounded():
    assert HistoryTextDiff.compare(original="a " * 2000, final="") is None
    assert HistoryTextDiff.compare(original="x" * 24_001, final="") is None


# --- settings navigation ----------------------------------------------------


def test_presenting_settings_records_where_to_return():
    state = SettingsNavigationState()
    assert not state.is_presented

    state.present(SettingsSection.AUDIO, returning_to=SidebarItem.HISTORY)
    assert state.is_presented
    assert state.selected_section is SettingsSection.AUDIO

    # Moving between sections must not overwrite the return destination.
    state.present(SettingsSection.GENERAL, returning_to=SidebarItem.STATS)
    assert state.return_destination is SidebarItem.HISTORY

    assert state.dismiss() is SidebarItem.HISTORY
    assert not state.is_presented


def test_presenting_without_a_destination_returns_to_welcome():
    state = SettingsNavigationState()
    state.present(SettingsSection.GENERAL, returning_to=None)
    assert state.dismiss() is SidebarItem.WELCOME


def test_is_leaving_only_reports_a_real_section_change():
    state = SettingsNavigationState()
    state.present(SettingsSection.AUDIO, returning_to=SidebarItem.WELCOME)

    assert state.is_leaving(SettingsSection.AUDIO, SettingsSection.GENERAL)
    assert state.is_leaving(SettingsSection.AUDIO, None)
    assert not state.is_leaving(SettingsSection.AUDIO, SettingsSection.AUDIO)
    assert not state.is_leaving(SettingsSection.GENERAL, SettingsSection.AUDIO)


def test_leaving_for_the_app_closes_settings_without_navigating_back():
    state = SettingsNavigationState()
    state.present(SettingsSection.AUDIO, returning_to=SidebarItem.HISTORY)
    state.leave_for_app()
    assert not state.is_presented
    assert state.return_destination is SidebarItem.HISTORY


def test_every_sidebar_item_and_settings_section_has_a_title_and_icon():
    for item in SidebarItem:
        assert item.title
        assert item.icon_name
    for section in SettingsSection:
        assert section.title
        assert section.icon_name
