"""Aggregate history into the numbers the Stats screen shows.

A port of `StatsSnapshot`. The snapshot is bounded and read-only: it keeps
counts, streaks and app names, never transcription text.

Streaks come in two flavours because a lot of people only dictate on working
days; "weekends don't break my streak" swaps the pair in without recomputing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Iterable, Sequence

from ..persistence.history_entry import TranscriptionHistoryEntry

WORD_MILESTONES = [(1_000, "1K"), (10_000, "10K"), (50_000, "50K"), (100_000, "100K"), (500_000, "500K"), (1_000_000, "1M")]
TRANSCRIPTION_MILESTONES = [(50, "50"), (100, "100"), (500, "500"), (1_000, "1K"), (5_000, "5K"), (10_000, "10K")]
STREAK_MILESTONES = [(7, "7 days"), (14, "14 days"), (30, "30 days"), (60, "60 days"), (100, "100 days"), (365, "1 year")]

TOTAL_MILESTONES_POSSIBLE = 18
ACTIVITY_DAYS = 30
SPEAKING_WORDS_PER_MINUTE = 150


def _is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def _previous_day(day: date, weekdays: bool) -> date | None:
    """The previous calendar day, or the previous *weekday* when asked."""
    candidate = day
    for _ in range(7):
        candidate = candidate - timedelta(days=1)
        if not weekdays or not _is_weekend(candidate):
            return candidate
    return None


def _streaks(days: Sequence[date], today: date, weekdays: bool) -> tuple[int, int]:
    """(current, best) run lengths over the days that had a dictation."""
    filtered = [day for day in days if not weekdays or not _is_weekend(day)]
    if not filtered:
        return (0, 0)

    first = filtered[0]
    latest = _previous_day(today, weekdays=True) if weekdays and _is_weekend(today) else today
    recent = latest is not None and (first == latest or first == _previous_day(latest, weekdays))

    initial_run = 1
    run = 1
    best = 1
    initial = True
    for index in range(1, len(filtered)):
        if filtered[index] == _previous_day(filtered[index - 1], weekdays):
            run += 1
            if initial:
                initial_run += 1
        else:
            initial = False
            run = 1
        best = max(best, run)
    return (initial_run if recent else 0, best)


@dataclass
class StatsSnapshot:
    total_words: int = 0
    total_transcriptions: int = 0
    ai_processed_count: int = 0
    longest_transcription_words: int = 0
    most_words_in_day: int = 0
    most_transcriptions_in_day: int = 0
    current_streak: int = 0
    best_streak: int = 0
    weekday_current_streak: int = 0
    weekday_best_streak: int = 0
    peak_hour_formatted: str = "N/A"
    top_apps: list[str] = field(default_factory=list)
    activity: list[tuple[date, int]] = field(default_factory=list)

    @property
    def average_words_per_transcription(self) -> int:
        if self.total_transcriptions == 0:
            return 0
        return self.total_words // self.total_transcriptions

    @property
    def ai_enhancement_rate(self) -> int:
        if self.total_transcriptions == 0:
            return 0
        return self.ai_processed_count * 100 // self.total_transcriptions

    def formatted_time_saved(self, typing_wpm: int) -> str:
        if typing_wpm <= 0:
            minutes = 0.0
        else:
            minutes = max(
                0.0,
                self.total_words / typing_wpm - self.total_words / SPEAKING_WORDS_PER_MINUTE,
            )
        if minutes < 1:
            return "< 1m"
        if minutes < 60:
            return f"{int(minutes)}m"
        hours = int(minutes) // 60
        remainder = int(minutes) % 60
        return f"{hours}h" if remainder == 0 else f"{hours}h {remainder}m"

    def daily_word_counts(self, days: int) -> list[tuple[date, int]]:
        return list(self.activity[-max(0, days) :]) if days > 0 else []

    def top_apps_formatted(self, limit: int) -> list[str]:
        return list(self.top_apps[: max(0, limit)])

    @property
    def word_milestones(self) -> list[tuple[int, bool, str]]:
        return [(target, self.total_words >= target, label) for target, label in WORD_MILESTONES]

    @property
    def transcription_milestones(self) -> list[tuple[int, bool, str]]:
        return [
            (target, self.total_transcriptions >= target, label)
            for target, label in TRANSCRIPTION_MILESTONES
        ]

    @property
    def streak_milestones(self) -> list[tuple[int, bool, str]]:
        return [(target, self.best_streak >= target, label) for target, label in STREAK_MILESTONES]

    @property
    def total_milestones_achieved(self) -> int:
        return (
            sum(1 for _, achieved, _ in self.word_milestones if achieved)
            + sum(1 for _, achieved, _ in self.transcription_milestones if achieved)
            + sum(1 for _, achieved, _ in self.streak_milestones if achieved)
        )

    total_milestones_possible: int = TOTAL_MILESTONES_POSSIBLE

    def using_weekdays(self, enabled: bool) -> "StatsSnapshot":
        if not enabled:
            return self
        from copy import copy

        result = copy(self)
        result.current_streak = self.weekday_current_streak
        result.best_streak = self.weekday_best_streak
        return result

    @staticmethod
    def build(
        entries: Iterable[TranscriptionHistoryEntry],
        now: datetime,
        zone: tzinfo | None = None,
    ) -> "StatsSnapshot":
        zone = zone or now.tzinfo
        result = StatsSnapshot()
        day_words: dict[date, int] = {}
        day_counts: dict[date, int] = {}
        app_counts: dict[str, int] = {}
        hours = [0] * 24

        entries = list(entries)
        result.total_transcriptions = len(entries)
        for entry in entries:
            words = len(entry.processed_text.split())
            local = entry.timestamp.astimezone(zone)
            day = local.date()
            day_words[day] = day_words.get(day, 0) + words
            day_counts[day] = day_counts.get(day, 0) + 1
            app_name = entry.app_name or "Unknown"
            app_counts[app_name] = app_counts.get(app_name, 0) + 1
            hours[local.hour] += 1
            result.total_words += words
            result.longest_transcription_words = max(result.longest_transcription_words, words)
            if entry.was_ai_processed:
                result.ai_processed_count += 1

        result.most_words_in_day = max(day_words.values(), default=0)
        result.most_transcriptions_in_day = max(day_counts.values(), default=0)
        # Ties break alphabetically so the list is stable between refreshes.
        result.top_apps = [
            name
            for name, _ in sorted(app_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]

        today = now.astimezone(zone).date()
        result.activity = [
            (today - timedelta(days=offset), day_words.get(today - timedelta(days=offset), 0))
            for offset in reversed(range(ACTIVITY_DAYS))
        ]

        days = sorted(day_counts.keys(), reverse=True)
        result.current_streak, result.best_streak = _streaks(days, today, weekdays=False)
        result.weekday_current_streak, result.weekday_best_streak = _streaks(
            days, today, weekdays=True
        )

        if entries:
            peak_hour = max(range(24), key=lambda hour: hours[hour])
            result.peak_hour_formatted = (
                f"{_format_hour(peak_hour)}-{_format_hour((peak_hour + 1) % 24)}"
            )
        return result


def _format_hour(hour: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display} {suffix}"
