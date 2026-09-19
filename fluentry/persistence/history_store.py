"""In-memory transcription history with durable backing.

The dictation history, in memory and on disk:

* edits made while the initial load is still running are merged into the
  loaded snapshot rather than lost,
* a failed write keeps the entry in memory and surfaces an error the user can
  retry, and a retry republishes the complete snapshot,
* the "today" counters are an event-driven snapshot: reading them never
  queries the clock or schedules a recount, and audio-only metadata updates
  never invalidate them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable, Iterable

from .history_database import TranscriptionHistoryWriter
from .history_entry import DictationAudioMetadata, TodaySummary, TranscriptionHistoryEntry
from .settings_types import now as utc_now


@dataclass(frozen=True)
class DayInterval:
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        """Half-open: `[start, end)`."""
        return self.start <= moment < self.end


def local_day_interval(moment: datetime, zone: tzinfo | None = None) -> DayInterval:
    """The calendar day containing `moment`, honouring DST-shortened days."""
    zone = zone or moment.astimezone().tzinfo
    local = moment.astimezone(zone)
    start_naive = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = _resolve_local_midnight(start_naive, zone)
    next_naive = (start.astimezone(zone) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = _resolve_local_midnight(next_naive, zone)
    return DayInterval(start=start, end=end)


def _resolve_local_midnight(candidate: datetime, zone: tzinfo) -> datetime:
    """Midnight can be skipped by a DST jump; walk forward to the real instant."""
    localized = candidate.replace(tzinfo=zone) if candidate.tzinfo is None else candidate.astimezone(zone)
    for _ in range(4):
        normalized = localized.astimezone(zone)
        if (normalized.hour, normalized.minute) == (0, 0):
            return normalized
        localized = localized + timedelta(hours=1)
    return candidate.astimezone(zone)


def count_words(text: str) -> int:
    return len(text.split())


def calculate_today_summary(
    entries: Iterable[TranscriptionHistoryEntry], day: DayInterval | None
) -> TodaySummary:
    if day is None:
        return TodaySummary(words=0, transcriptions=0)
    words = 0
    transcriptions = 0
    for entry in entries:
        if not day.contains(entry.timestamp):
            continue
        words += count_words(entry.processed_text)
        transcriptions += 1
    return TodaySummary(words=words, transcriptions=transcriptions)


def time_saved_minutes(summary: TodaySummary, typing_wpm: int = 40, speaking_wpm: int = 150) -> float:
    if typing_wpm <= 0 or speaking_wpm <= 0:
        return 0.0
    words = float(summary.words)
    return max(0.0, words / typing_wpm - words / speaking_wpm)


def formatted_time_saved(summary: TodaySummary, typing_wpm: int = 40) -> str:
    minutes = time_saved_minutes(summary, typing_wpm=typing_wpm)
    if minutes < 1:
        return "< 1m"
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = int(minutes // 60)
    remaining = int(minutes % 60)
    return f"{hours}h" if remaining == 0 else f"{hours}h {remaining}m"


class _NullAudioStore:
    """Used until a real audio store is attached (and by tests)."""

    def delete_audio(self, file_name: str) -> int:
        return 0

    def delete_all_audio_files(self) -> None:
        return None

    def audio_usage_bytes(self) -> int:
        return 0

    def delete_unreferenced_audio_files(self, referenced_file_names: set[str]) -> tuple[int, int]:
        return (0, 0)


class TranscriptionHistoryStore:
    def __init__(
        self,
        writer: TranscriptionHistoryWriter | None = None,
        summary_now: Callable[[], datetime] | None = None,
        summary_timezone: Callable[[], tzinfo] | None = None,
        audio_store=None,
        audio_budget_bytes: Callable[[], int] | None = None,
        load: bool = True,
    ) -> None:
        self.writer = writer or TranscriptionHistoryWriter()
        self._summary_now = summary_now or utc_now
        self._summary_timezone = summary_timezone or (lambda: datetime.now().astimezone().tzinfo)
        self.audio_store = audio_store or _NullAudioStore()
        self._audio_budget_bytes = audio_budget_bytes or (lambda: 1_000_000_000)

        self.entries: list[TranscriptionHistoryEntry] = []
        self.selected_entry_id: str | None = None
        self.today_summary = TodaySummary(words=0, transcriptions=0)
        self.is_loading = True
        self.persistence_error: str | None = None

        self._has_loaded = False
        self._pending_upserts: dict[str, TranscriptionHistoryEntry] = {}
        self._pending_deletes: set[str] = set()
        self._pending_replacement = False
        self._today_summary_day: DayInterval | None = None
        self._today_summary_revision = 0
        self._in_summary_refresh = False
        self._audio_save_generation = 0
        self._observers: list[Callable[[TodaySummary], None]] = []
        self._load_thread: threading.Thread | None = None

        if load:
            self.load_entries()

    # --- observation ------------------------------------------------------

    def add_today_summary_observer(self, callback: Callable[[TodaySummary], None]) -> Callable[[], None]:
        self._observers.append(callback)

        def remove() -> None:
            if callback in self._observers:
                self._observers.remove(callback)

        return remove

    def _publish_today_summary(self) -> None:
        for observer in list(self._observers):
            try:
                observer(self.today_summary)
            except Exception:
                pass

    # --- loading ----------------------------------------------------------

    def load_entries(self) -> None:
        self.is_loading = True

        def work() -> None:
            try:
                loaded = self.writer.load()
                if self._pending_replacement:
                    merged: list[TranscriptionHistoryEntry] = []
                else:
                    merged = [
                        entry
                        for entry in loaded
                        if entry.id not in self._pending_deletes and entry.id not in self._pending_upserts
                    ]
                merged.extend(self._pending_upserts.values())
                self.entries = sorted(merged, key=lambda entry: entry.timestamp, reverse=True)
                self._refresh_today_summary()
                self._has_loaded = True
                self.persistence_error = None
                if self._pending_replacement or self._pending_upserts or self._pending_deletes:
                    self._persist(
                        upserts=list(self._pending_upserts.values()),
                        deletes=list(self._pending_deletes),
                        replacing=self._pending_replacement,
                    )
                self._pending_upserts.clear()
                self._pending_deletes.clear()
                self._pending_replacement = False
            except Exception as error:
                self.persistence_error = (
                    "History could not be loaded. New dictations are kept in memory "
                    f"until you retry. {error}"
                )
            finally:
                self.is_loading = False

        thread = threading.Thread(target=work, name="fluid.history.load", daemon=True)
        self._load_thread = thread
        thread.start()

    def wait_until_loaded(self, timeout: float = 60.0) -> None:
        thread = self._load_thread
        if thread is not None:
            thread.join(timeout)
        if not self._has_loaded:
            raise RuntimeError(self.persistence_error or "History is unavailable.")

    def finish_pending_writes(self, timeout: float = 60.0) -> None:
        thread = self._load_thread
        if thread is not None:
            thread.join(timeout)
        error = self.writer.drain(timeout)
        if error is not None:
            self.persistence_error = (
                f"History could not be saved. Keep Fluentry open and retry. {error}"
            )

    def retry_persistence(self) -> None:
        if self.is_loading:
            return
        if not self._has_loaded:
            self.load_entries()
            return
        self.writer.write(
            upserts=list(self.entries), replacing=True, completion=self._write_completion
        )

    def _write_completion(self, error: Exception | None) -> None:
        if error is None:
            self.persistence_error = None
            return
        self.persistence_error = (
            f"History could not be saved. Keep Fluentry open and retry. {error}"
        )

    # --- mutation ---------------------------------------------------------

    def add_entry(
        self,
        raw_text: str,
        processed_text: str,
        app_name: str,
        window_title: str,
        entry_id: str | None = None,
        timestamp: datetime | None = None,
        was_ai_processed: bool | None = None,
        processing_model: str | None = None,
        transcription_duration_milliseconds: int | None = None,
        ai_processing_duration_milliseconds: int | None = None,
        ai_tokens_per_second: float | None = None,
        ai_processing_error: str | None = None,
        audio: DictationAudioMetadata | None = None,
    ) -> TranscriptionHistoryEntry | None:
        if not processed_text.strip():
            return None

        kwargs: dict = {
            "raw_text": raw_text,
            "processed_text": processed_text,
            "app_name": app_name,
            "window_title": window_title,
            "was_ai_processed": (
                was_ai_processed
                if was_ai_processed is not None
                else (processing_model is not None and ai_processing_error is None)
            ),
            "processing_model": processing_model,
            "transcription_duration_milliseconds": transcription_duration_milliseconds,
            "ai_processing_duration_milliseconds": ai_processing_duration_milliseconds,
            "ai_tokens_per_second": ai_tokens_per_second,
            "ai_processing_error": ai_processing_error,
            "audio": audio,
        }
        if entry_id is not None:
            kwargs["id"] = entry_id
        if timestamp is not None:
            kwargs["timestamp"] = timestamp
        entry = TranscriptionHistoryEntry(**kwargs)

        self.entries.insert(0, entry)
        self._refresh_today_summary()
        self._persist(upserts=[entry])
        return entry

    def delete_entry(self, entry_id: str) -> None:
        existing = next((entry for entry in self.entries if entry.id == entry_id), None)
        if existing is not None and existing.audio is not None:
            self.audio_store.delete_audio(existing.audio.file_name)
        previous_count = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.id != entry_id]
        if len(self.entries) != previous_count:
            self._refresh_today_summary()
        if self.selected_entry_id == entry_id:
            self.selected_entry_id = self.entries[0].id if self.entries else None
        self._persist(deletes=[entry_id])

    def delete_entries(self, ids: Iterable[str]) -> None:
        identifiers = set(ids)
        for entry in self.entries:
            if entry.id in identifiers and entry.audio is not None:
                self.audio_store.delete_audio(entry.audio.file_name)
        previous_count = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.id not in identifiers]
        if len(self.entries) != previous_count:
            self._refresh_today_summary()
        if self.selected_entry_id in identifiers:
            self.selected_entry_id = self.entries[0].id if self.entries else None
        self._persist(deletes=list(identifiers))

    def prune_expired_entries(
        self, interval, now: datetime | None = None
    ) -> int:
        """Delete entries past the retention window, audio included.

        Returns how many went. Does nothing when the interval is Never,
        which is the default — history only expires if the user asks.
        """
        cutoff = interval.cutoff(now)
        if cutoff is None:
            return 0

        expired = [entry for entry in self.entries if entry.timestamp < cutoff]
        if not expired:
            return 0

        for entry in expired:
            if entry.audio is not None:
                self.audio_store.delete_audio(entry.audio.file_name)

        identifiers = [entry.id for entry in expired]
        self.entries = [entry for entry in self.entries if entry.timestamp >= cutoff]
        self._refresh_today_summary()
        if self.selected_entry_id in set(identifiers):
            self.selected_entry_id = self.entries[0].id if self.entries else None
        self._persist(deletes=identifiers)
        return len(expired)

    def clear_all_history(self) -> None:
        self._audio_save_generation += 1
        self.audio_store.delete_all_audio_files()
        self.entries = []
        self._refresh_today_summary()
        self.selected_entry_id = None
        self._persist(replacing=True)

    def restore(self, payload: list[TranscriptionHistoryEntry]) -> None:
        self._audio_save_generation += 1
        self.entries = sorted(payload, key=lambda entry: entry.timestamp, reverse=True)
        self._refresh_today_summary()
        self.selected_entry_id = self.entries[0].id if self.entries else None
        self._persist(upserts=list(self.entries), replacing=True)

    def attach_audio(
        self,
        audio: DictationAudioMetadata,
        entry_id: str,
        expected_save_generation: int | None = None,
    ) -> None:
        if expected_save_generation is not None and expected_save_generation != self._audio_save_generation:
            self.audio_store.delete_audio(audio.file_name)
            return
        index = next((i for i, entry in enumerate(self.entries) if entry.id == entry_id), None)
        if index is None:
            self.audio_store.delete_audio(audio.file_name)
            return
        self.entries[index] = self.entries[index].replacing_audio(audio)
        # Deliberately no summary refresh: audio metadata cannot change word counts.
        self._persist(upserts=[self.entries[index]])

    def delete_all_saved_audio(self) -> int:
        if not self._has_loaded:
            return 0
        self._audio_save_generation += 1
        removed_count = sum(1 for entry in self.entries if entry.audio is not None)
        self.audio_store.delete_all_audio_files()
        changed = [entry.replacing_audio(None) for entry in self.entries if entry.audio is not None]
        self.entries = [entry.replacing_audio(None) for entry in self.entries]
        self._persist(upserts=changed)
        return removed_count

    def prune_audio_to_budget(
        self, current_bytes: int | None = None, budget_bytes: int | None = None
    ) -> int:
        # An incomplete startup snapshot must never classify older recordings as orphaned.
        if not self._has_loaded:
            return 0
        budget = budget_bytes if budget_bytes is not None else self._audio_budget_bytes()
        if budget <= 0:
            return self.delete_all_saved_audio()
        current = current_bytes if current_bytes is not None else self.audio_store.audio_usage_bytes()
        if current <= budget:
            return 0

        updated = list(self.entries)
        referenced = {entry.audio.file_name for entry in updated if entry.audio is not None}
        orphan_count, orphan_bytes = self.audio_store.delete_unreferenced_audio_files(referenced)
        if orphan_count > 0:
            current = max(0, current - orphan_bytes)
        if current <= budget:
            return 0

        pruned = 0
        changed: list[TranscriptionHistoryEntry] = []
        for index in reversed(range(len(updated))):
            audio = updated[index].audio
            if audio is None:
                continue
            removed_bytes = self.audio_store.delete_audio(audio.file_name)
            current = max(0, current - removed_bytes)
            updated[index] = updated[index].replacing_audio(None)
            changed.append(updated[index])
            pruned += 1
            if current <= budget:
                break

        if pruned > 0:
            self.entries = updated
            self._persist(upserts=changed)
        return pruned

    # --- queries ----------------------------------------------------------

    @property
    def selected_entry(self) -> TranscriptionHistoryEntry | None:
        if self.selected_entry_id is None:
            return None
        return next((entry for entry in self.entries if entry.id == self.selected_entry_id), None)

    @property
    def latest_clipboard_text(self) -> str | None:
        return self.entries[0].clipboard_text if self.entries else None

    def search(self, query: str) -> list[TranscriptionHistoryEntry]:
        if not query.strip():
            return list(self.entries)
        lowercased = query.lower()
        return [
            entry
            for entry in self.entries
            if lowercased in entry.raw_text.lower()
            or lowercased in entry.processed_text.lower()
            or lowercased in entry.app_name.lower()
            or lowercased in entry.window_title.lower()
        ]

    def entries_in_range(self, start: datetime, end: datetime) -> list[TranscriptionHistoryEntry]:
        return [entry for entry in self.entries if start <= entry.timestamp <= end]

    @property
    def total_character_count(self) -> int:
        return sum(entry.character_count or 0 for entry in self.entries)

    @property
    def ai_processed_count(self) -> int:
        return sum(1 for entry in self.entries if entry.was_ai_processed)

    def make_backup_payload(self) -> list[TranscriptionHistoryEntry]:
        return list(self.entries)

    # --- persistence ------------------------------------------------------

    def _persist(
        self,
        upserts: list[TranscriptionHistoryEntry] | None = None,
        deletes: list[str] | None = None,
        replacing: bool = False,
    ) -> None:
        upserts = upserts or []
        deletes = deletes or []
        if not self._has_loaded:
            if replacing:
                self._pending_replacement = True
                self._pending_upserts.clear()
                self._pending_deletes.clear()
            for identifier in deletes:
                self._pending_upserts.pop(identifier, None)
                self._pending_deletes.add(identifier)
            for entry in upserts:
                self._pending_deletes.discard(entry.id)
                self._pending_upserts[entry.id] = entry
            return
        self.writer.write(
            upserts=upserts,
            deletes=deletes,
            replacing=replacing,
            completion=lambda error: None if error is None else self._write_completion(error),
        )

    # --- today summary ----------------------------------------------------

    def refresh_today_summary_for_calendar_change(self) -> None:
        day = local_day_interval(self._summary_now(), self._summary_timezone())
        if day == self._today_summary_day:
            return
        self._refresh_today_summary()

    def _refresh_today_summary(self) -> None:
        self._today_summary_revision += 1
        if self._in_summary_refresh:
            # A reentrant change during publication is picked up by the loop below.
            return
        self._in_summary_refresh = True
        try:
            while True:
                revision = self._today_summary_revision
                day = local_day_interval(self._summary_now(), self._summary_timezone())
                summary = calculate_today_summary(list(self.entries), day)
                if revision != self._today_summary_revision:
                    continue
                self._today_summary_day = day
                changed = self.today_summary != summary
                self.today_summary = summary
                if changed:
                    self._publish_today_summary()
                if revision == self._today_summary_revision:
                    return
        finally:
            self._in_summary_refresh = False

    def wait_for_today_summary(self) -> None:
        """Present so callers can await a flush; writes are already done."""
        return None
