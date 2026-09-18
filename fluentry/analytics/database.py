"""SQLite aggregates and a crash-safe upload outbox.

A port of `AnalyticsDatabase`. The privacy-shaped behaviours are what matter
and are preserved exactly:

* raw usage is aggregated per day and only leaves as a daily summary,
* daily-activity events are held back until the week they belong to has
  ended, so a single event cannot pinpoint a day of use,
* durations are reported as buckets, never as raw values,
* opting out of detailed analytics purges everything except daily activity,
  and rejects work that was queued before the opt-out.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date as date_type, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable, Sequence

from .events import (
    ActivityKind,
    AnalyticsEvent,
    ModelDescriptor,
    ModelDownloadOutcome,
    ModelDownloadSource,
    ModelRole,
    OnboardingOrigin,
    OnboardingOutcome,
    OnboardingStep,
    TryoutFailureStage,
    TryoutOutcome,
    TryoutStartMethod,
    UsageMode,
)
from .system_configuration import AnalyticsSystemConfiguration

PERFORMANCE_BUCKET_UPPER_BOUNDS = [
    25, 50, 75, 100, 150, 200, 300, 500, 750, 1000,
    1500, 2000, 3000, 5000, 7500, 10_000, 20_000, 60_000,
]

SCHEMA_VERSION = 2
DEDUPE_RETENTION = timedelta(days=45)
MAXIMUM_MEASURED_MILLISECONDS = 600_000


class AnalyticsDatabaseError(Exception):
    pass


@dataclass(frozen=True)
class AnalyticsOutboxItem:
    id: str
    payload: bytes


class DetailedAnalyticsConsentGate:
    """Rejects work queued before the user opted out.

    Analytics work is queued asynchronously, so an opt-out has to invalidate
    everything already in flight rather than only future events.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def advance(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def accepts(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AnalyticsDatabase:
    def __init__(
        self,
        path: Path,
        distinct_id: str,
        app_version: str,
        system_configuration: AnalyticsSystemConfiguration | None = None,
        zone: tzinfo = timezone.utc,
        first_weekday: int = 0,  # 0 = Monday, matching Calendar.firstWeekday = 2
    ) -> None:
        self.path = Path(path)
        self.distinct_id = distinct_id
        self.app_version = app_version
        self.system_configuration = system_configuration or AnalyticsSystemConfiguration.current()
        self.zone = zone
        self.first_weekday = first_weekday

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        # Manual transaction control: the explicit BEGIN IMMEDIATE blocks below
        # are the only transactions, so sqlite3's implicit ones cannot nest.
        self._connection.isolation_level = None
        self._lock = threading.RLock()
        try:
            self._execute("PRAGMA journal_mode=WAL")
            self._execute("PRAGMA synchronous=FULL")
            self._execute("PRAGMA secure_delete=ON")
            self._execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._execute("PRAGMA busy_timeout=3000")
            self._create_schema()
        except Exception:
            self._connection.close()
            raise

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            pass

    # --- sqlite helpers ---------------------------------------------------

    def _execute(self, sql: str, parameters: Sequence = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, parameters)
        except sqlite3.Error as error:
            raise AnalyticsDatabaseError(str(error)) from error

    def _query(self, sql: str, parameters: Sequence = ()) -> list[tuple]:
        return self._execute(sql, parameters).fetchall()

    class _Transaction:
        def __init__(self, database: "AnalyticsDatabase") -> None:
            self.database = database

        def __enter__(self) -> None:
            self.database._execute("BEGIN IMMEDIATE")

        def __exit__(self, exc_type, exc, traceback) -> bool:
            if exc_type is None:
                self.database._execute("COMMIT")
            else:
                try:
                    self.database._execute("ROLLBACK")
                except AnalyticsDatabaseError:
                    pass
            return False

    def _transaction(self) -> "_Transaction":
        return AnalyticsDatabase._Transaction(self)

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY,
                event_name TEXT NOT NULL,
                payload BLOB NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS outbox_ready ON outbox(next_retry_at, created_at);
            CREATE TABLE IF NOT EXISTS daily_usage (
                day TEXT PRIMARY KEY,
                dictation_count INTEGER NOT NULL DEFAULT 0,
                command_count INTEGER NOT NULL DEFAULT 0,
                edit_count INTEGER NOT NULL DEFAULT 0,
                meeting_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daily_model_usage (
                day TEXT NOT NULL,
                role TEXT NOT NULL,
                mode TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day, role, mode, provider, model)
            );
            CREATE TABLE IF NOT EXISTS daily_dictation_performance (
                day TEXT NOT NULL,
                measured_app_version TEXT NOT NULL,
                measured_os_version TEXT NOT NULL,
                metric TEXT NOT NULL,
                bucket_index INTEGER NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day, measured_app_version, measured_os_version, metric, bucket_index)
            );
            CREATE TABLE IF NOT EXISTS event_dedupe (
                dedupe_key TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS onboarding_flows (
                flow_id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                created_at REAL NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS onboarding_tryout_state (
                flow_id TEXT PRIMARY KEY,
                entered_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_start_method TEXT,
                last_outcome TEXT,
                last_failure_stage TEXT
            );
            CREATE TABLE IF NOT EXISTS model_download_attempts (
                download_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                source TEXT NOT NULL,
                started_at REAL NOT NULL
            );
            """
        )

    # --- time helpers -----------------------------------------------------

    def _day_string(self, moment: datetime) -> str:
        return moment.astimezone(self.zone).strftime("%Y-%m-%d")

    def _week_start(self, moment: datetime) -> float:
        local = moment.astimezone(self.zone)
        start_of_day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        offset = (start_of_day.weekday() - self.first_weekday) % 7
        return (start_of_day - timedelta(days=offset)).timestamp()

    # --- enqueue ----------------------------------------------------------

    def _enqueue(self, event: AnalyticsEvent, moment: datetime, properties: dict[str, Any]) -> None:
        event_id = str(uuid.uuid4()).lower()
        approved = dict(properties)
        approved["distinct_id"] = self.distinct_id
        approved["platform"] = "linux"
        approved["$os"] = "Linux"
        approved["app_version"] = self.app_version
        approved["ram_gb"] = self.system_configuration.ram_gb
        approved["chip"] = self.system_configuration.chip
        approved["$set"] = {
            "ram_gb": self.system_configuration.ram_gb,
            "chip": self.system_configuration.chip,
        }
        approved["schema_version"] = SCHEMA_VERSION

        payload = {
            "uuid": event_id,
            "event": event.value,
            "timestamp": _iso(moment),
            "properties": approved,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise AnalyticsDatabaseError("Invalid analytics payload") from error
        self._execute(
            "INSERT INTO outbox (event_id, event_name, payload, created_at) VALUES (?, ?, ?, ?)",
            (event_id, event.value, sqlite3.Binary(data), moment.timestamp()),
        )

    def _insert_dedupe_key(self, key: str, moment: datetime) -> bool:
        cursor = self._execute(
            "INSERT OR IGNORE INTO event_dedupe (dedupe_key, created_at) VALUES (?, ?)",
            (key, moment.timestamp()),
        )
        return cursor.rowcount > 0

    def _dedupe_key_exists(self, key: str) -> bool:
        rows = self._query("SELECT COUNT(*) FROM event_dedupe WHERE dedupe_key = ?", (key,))
        return bool(rows) and rows[0][0] == 1

    def _enqueue_activity_if_needed(self, kind: ActivityKind, moment: datetime) -> None:
        day = self._day_string(moment)
        if not self._insert_dedupe_key(f"activity:{day}", moment):
            return
        self._enqueue(
            AnalyticsEvent.ACTIVE_USER,
            moment,
            {"activity_kind": kind.value, "activity_date": day},
        )

    # --- recording --------------------------------------------------------

    def record_activity(self, kind: ActivityKind, moment: datetime) -> None:
        self.finalize_days(before=moment)
        with self._lock, self._transaction():
            self._enqueue_activity_if_needed(kind, moment)

    def record_usage(
        self,
        mode: UsageMode,
        transcription_model: ModelDescriptor | None,
        ai_model: ModelDescriptor | None,
        moment: datetime,
    ) -> None:
        self.finalize_days(before=moment)
        day = self._day_string(moment)
        with self._lock, self._transaction():
            self._upsert_daily_usage(day, mode)
            if transcription_model is not None:
                self._upsert_daily_model_usage(
                    day, ModelRole.TRANSCRIPTION, mode, transcription_model
                )
            if ai_model is not None:
                self._upsert_daily_model_usage(
                    day, ModelRole.AI_POST_PROCESSING, mode, ai_model
                )
            self._enqueue_activity_if_needed(ActivityKind.CORE_ACTION, moment)

    def record_model_usage(
        self, role: ModelRole, mode: UsageMode, descriptor: ModelDescriptor, moment: datetime
    ) -> None:
        self.finalize_days(before=moment)
        with self._lock, self._transaction():
            self._upsert_daily_model_usage(self._day_string(moment), role, mode, descriptor)
            self._enqueue_activity_if_needed(ActivityKind.CORE_ACTION, moment)

    def _upsert_daily_usage(self, day: str, mode: UsageMode) -> None:
        column = {
            UsageMode.DICTATION: "dictation_count",
            UsageMode.COMMAND: "command_count",
            UsageMode.EDIT: "edit_count",
            UsageMode.MEETING: "meeting_count",
        }[mode]
        self._execute(
            f"INSERT INTO daily_usage (day, {column}) VALUES (?, 1) "
            f"ON CONFLICT(day) DO UPDATE SET {column} = {column} + 1",
            (day,),
        )

    def _upsert_daily_model_usage(
        self, day: str, role: ModelRole, mode: UsageMode, descriptor: ModelDescriptor
    ) -> None:
        self._execute(
            "INSERT INTO daily_model_usage (day, role, mode, provider, model, use_count) "
            "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(day, role, mode, provider, model) "
            "DO UPDATE SET use_count = use_count + 1",
            (day, role.value, mode.value, descriptor.provider, descriptor.model),
        )

    def record_dictation_performance(
        self,
        asr_milliseconds: int | None,
        enhancement_milliseconds: int | None,
        measured_app_version: str,
        moment: datetime,
    ) -> None:
        if asr_milliseconds is None and enhancement_milliseconds is None:
            return
        self.finalize_days(before=moment)
        day = self._day_string(moment)
        with self._lock, self._transaction():
            if asr_milliseconds is not None:
                self._upsert_performance_metric(day, measured_app_version, "asr", asr_milliseconds)
            if enhancement_milliseconds is not None:
                self._upsert_performance_metric(
                    day, measured_app_version, "fluid_intelligence", enhancement_milliseconds
                )

    def _upsert_performance_metric(
        self, day: str, measured_app_version: str, metric: str, milliseconds: int
    ) -> None:
        bounded = min(max(milliseconds, 0), MAXIMUM_MEASURED_MILLISECONDS)
        bucket_index = next(
            (
                index
                for index, upper in enumerate(PERFORMANCE_BUCKET_UPPER_BOUNDS)
                if bounded <= upper
            ),
            len(PERFORMANCE_BUCKET_UPPER_BOUNDS),
        )
        self._execute(
            "INSERT INTO daily_dictation_performance "
            "(day, measured_app_version, measured_os_version, metric, bucket_index, sample_count) "
            "VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(day, measured_app_version, measured_os_version, metric, bucket_index) "
            "DO UPDATE SET sample_count = sample_count + 1",
            (
                day,
                measured_app_version,
                self.system_configuration.os_version,
                metric,
                bucket_index,
            ),
        )

    # --- onboarding -------------------------------------------------------

    def _active_onboarding_flow(self, origin: OnboardingOrigin, moment: datetime) -> str:
        rows = self._query(
            "SELECT flow_id FROM onboarding_flows WHERE completed = 0 AND origin = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (origin.value,),
        )
        if rows:
            return rows[0][0]
        flow_id = str(uuid.uuid4()).lower()
        self._execute(
            "INSERT INTO onboarding_flows (flow_id, origin, created_at) VALUES (?, ?, ?)",
            (flow_id, origin.value, moment.timestamp()),
        )
        return flow_id

    def _ensure_tryout_state(self, flow_id: str, entered_at: datetime) -> None:
        self._execute(
            "INSERT OR IGNORE INTO onboarding_tryout_state (flow_id, entered_at) VALUES (?, ?)",
            (flow_id, entered_at.timestamp()),
        )

    def _tryout_state(self, flow_id: str) -> tuple[float, int, str | None, str | None]:
        rows = self._query(
            "SELECT entered_at, attempt_count, COALESCE(last_start_method, ''), "
            "COALESCE(last_failure_stage, '') FROM onboarding_tryout_state WHERE flow_id = ?",
            (flow_id,),
        )
        if not rows:
            raise AnalyticsDatabaseError("Missing onboarding tryout state")
        entered_at, attempt_count, start_method, failure_stage = rows[0]
        return (
            float(entered_at),
            int(attempt_count),
            start_method or None,
            failure_stage or None,
        )

    def record_onboarding_started(self, origin: OnboardingOrigin, moment: datetime) -> None:
        self.finalize_days(before=moment)
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            if self._insert_dedupe_key(f"onboarding:{flow_id}:started", moment):
                self._enqueue(
                    AnalyticsEvent.ONBOARDING_STARTED,
                    moment,
                    {"flow_id": flow_id, "origin": origin.value},
                )
            self._enqueue_activity_if_needed(ActivityKind.APP, moment)

    def record_onboarding_step_viewed(
        self, step: OnboardingStep, origin: OnboardingOrigin, moment: datetime
    ) -> None:
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            if step is OnboardingStep.PLAYGROUND:
                self._ensure_tryout_state(flow_id, moment)
            if not self._insert_dedupe_key(f"onboarding:{flow_id}:viewed:{step.value}", moment):
                return
            self._enqueue(
                AnalyticsEvent.ONBOARDING_STEP_VIEWED,
                moment,
                {"flow_id": flow_id, "origin": origin.value, "step": step.value},
            )

    def record_onboarding_step_completed(
        self,
        step: OnboardingStep,
        outcome: OnboardingOutcome,
        origin: OnboardingOrigin,
        completes_flow: bool,
        moment: datetime,
    ) -> None:
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            if self._insert_dedupe_key(f"onboarding:{flow_id}:completed:{step.value}", moment):
                self._enqueue(
                    AnalyticsEvent.ONBOARDING_STEP_COMPLETED,
                    moment,
                    {
                        "flow_id": flow_id,
                        "origin": origin.value,
                        "step": step.value,
                        "outcome": outcome.value,
                    },
                )
            if completes_flow:
                if self._insert_dedupe_key(f"onboarding:{flow_id}:flow_completed", moment):
                    self._enqueue(
                        AnalyticsEvent.ONBOARDING_COMPLETED,
                        moment,
                        {"flow_id": flow_id, "origin": origin.value},
                    )
                self._execute(
                    "UPDATE onboarding_flows SET completed = 1 WHERE flow_id = ?", (flow_id,)
                )

    def record_onboarding_tryout_attempt_started(
        self, start_method: TryoutStartMethod, origin: OnboardingOrigin, moment: datetime
    ) -> None:
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            self._ensure_tryout_state(flow_id, moment)
            self._execute(
                "UPDATE onboarding_tryout_state SET attempt_count = attempt_count + 1, "
                "last_start_method = ?, last_outcome = NULL, last_failure_stage = NULL "
                "WHERE flow_id = ?",
                (start_method.value, flow_id),
            )

    def record_onboarding_tryout_attempt_result(
        self,
        outcome: TryoutOutcome,
        failure_stage: TryoutFailureStage | None,
        origin: OnboardingOrigin,
        moment: datetime,
    ) -> None:
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            self._ensure_tryout_state(flow_id, moment)
            self._execute(
                "UPDATE onboarding_tryout_state SET last_outcome = ?, last_failure_stage = ? "
                "WHERE flow_id = ?",
                (outcome.value, failure_stage.value if failure_stage else None, flow_id),
            )

    def finish_onboarding_tryout(
        self,
        outcome: TryoutOutcome,
        failure_stage: TryoutFailureStage | None,
        origin: OnboardingOrigin,
        moment: datetime,
    ) -> None:
        with self._lock, self._transaction():
            flow_id = self._active_onboarding_flow(origin, moment)
            self._ensure_tryout_state(flow_id, moment)
            entered_at, attempt_count, start_method, last_failure_stage = self._tryout_state(flow_id)
            if not self._insert_dedupe_key(f"onboarding:{flow_id}:tryout_finished", moment):
                return

            properties: dict[str, Any] = {
                "flow_id": flow_id,
                "origin": origin.value,
                "outcome": outcome.value,
                "duration_bucket": tryout_duration_bucket(moment.timestamp() - entered_at),
            }
            if attempt_count > 0:
                properties["attempt_count_bucket"] = tryout_attempt_count_bucket(attempt_count)
            if start_method is not None:
                properties["start_method"] = start_method
            if failure_stage is not None:
                properties["failure_stage"] = failure_stage.value
            elif outcome is TryoutOutcome.SKIPPED_AFTER_ATTEMPT and last_failure_stage:
                properties["failure_stage"] = last_failure_stage
            self._enqueue(AnalyticsEvent.ONBOARDING_TRYOUT_FINISHED, moment, properties)

    def skip_onboarding_tryout(self, origin: OnboardingOrigin, moment: datetime) -> None:
        with self._lock:
            flow_id = self._active_onboarding_flow(origin, moment)
            self._ensure_tryout_state(flow_id, moment)
            _, attempt_count, _, _ = self._tryout_state(flow_id)
        outcome = (
            TryoutOutcome.SKIPPED_BEFORE_ATTEMPT
            if attempt_count == 0
            else TryoutOutcome.SKIPPED_AFTER_ATTEMPT
        )
        self.finish_onboarding_tryout(outcome, None, origin, moment)

    # --- model downloads --------------------------------------------------

    def record_model_download_started(
        self,
        download_id: str,
        descriptor: ModelDescriptor,
        source: ModelDownloadSource,
        moment: datetime,
    ) -> None:
        self.finalize_days(before=moment)
        with self._lock, self._transaction():
            # A finish that already arrived wins: never report a start after it.
            if not self._dedupe_key_exists(f"download:{download_id}:finished") and (
                self._insert_dedupe_key(f"download:{download_id}:started", moment)
            ):
                self._execute(
                    "INSERT OR IGNORE INTO model_download_attempts "
                    "(download_id, provider, model, source, started_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        download_id,
                        descriptor.provider,
                        descriptor.model,
                        source.value,
                        moment.timestamp(),
                    ),
                )
                self._enqueue(
                    AnalyticsEvent.MODEL_DOWNLOAD_STARTED,
                    moment,
                    {
                        "download_id": download_id,
                        "provider": descriptor.provider,
                        "model": descriptor.model,
                        "source": source.value,
                    },
                )
            self._enqueue_activity_if_needed(ActivityKind.CORE_ACTION, moment)

    def record_model_download_finished(
        self,
        download_id: str,
        descriptor: ModelDescriptor,
        source: ModelDownloadSource,
        outcome: ModelDownloadOutcome,
        duration: float | None,
        moment: datetime,
    ) -> None:
        with self._lock, self._transaction():
            if not self._insert_dedupe_key(f"download:{download_id}:finished", moment):
                return
            if not self._model_download_exists(download_id):
                # A finish with no recorded start still reports a complete pair.
                inferred_start = (
                    moment - timedelta(seconds=max(0.0, duration)) if duration is not None else moment
                )
                self._insert_dedupe_key(f"download:{download_id}:started", inferred_start)
                self._enqueue(
                    AnalyticsEvent.MODEL_DOWNLOAD_STARTED,
                    inferred_start,
                    {
                        "download_id": download_id,
                        "provider": descriptor.provider,
                        "model": descriptor.model,
                        "source": source.value,
                    },
                )
            properties: dict[str, Any] = {
                "download_id": download_id,
                "provider": descriptor.provider,
                "model": descriptor.model,
                "source": source.value,
                "outcome": outcome.value,
            }
            if duration is not None and duration >= 0:
                properties["duration_seconds"] = round(duration * 10) / 10
            self._enqueue(AnalyticsEvent.MODEL_DOWNLOAD_FINISHED, moment, properties)
            self._execute(
                "DELETE FROM model_download_attempts WHERE download_id = ?", (download_id,)
            )

    def _model_download_exists(self, download_id: str) -> bool:
        rows = self._query(
            "SELECT COUNT(*) FROM model_download_attempts WHERE download_id = ?", (download_id,)
        )
        return bool(rows) and rows[0][0] == 1

    def recover_interrupted_model_downloads(self, moment: datetime) -> None:
        """A download in progress when the app died is reported as interrupted."""
        rows = self._query(
            "SELECT download_id, provider, model, source FROM model_download_attempts"
        )
        for download_id, provider, model, source in rows:
            self.record_model_download_finished(
                download_id=download_id,
                descriptor=ModelDescriptor(provider=provider, model=model),
                source=_download_source(source),
                outcome=ModelDownloadOutcome.INTERRUPTED,
                duration=None,
                moment=moment,
            )

    # --- daily finalization -----------------------------------------------

    def finalize_days(self, before: datetime) -> None:
        today = self._day_string(before)
        with self._lock:
            usage_rows = self._query(
                "SELECT day, dictation_count, command_count, edit_count, meeting_count "
                "FROM daily_usage WHERE day < ? ORDER BY day",
                (today,),
            )
            model_rows = self._query(
                "SELECT day, role, mode, provider, model, use_count FROM daily_model_usage "
                "WHERE day < ? ORDER BY day, role, mode, provider, model",
                (today,),
            )
            performance_rows = self._query(
                "SELECT day, measured_app_version, measured_os_version, metric, bucket_index, "
                "sample_count FROM daily_dictation_performance WHERE day < ? "
                "ORDER BY day, measured_app_version, measured_os_version, metric, bucket_index",
                (today,),
            )
            if not usage_rows and not model_rows and not performance_rows:
                return

            with self._transaction():
                for row in usage_rows:
                    self._enqueue(
                        AnalyticsEvent.USAGE_DAILY_SUMMARY,
                        before,
                        {
                            "usage_date": row[0],
                            "dictation_count": int(row[1]),
                            "command_count": int(row[2]),
                            "edit_count": int(row[3]),
                            "meeting_count": int(row[4]),
                        },
                    )
                for row in model_rows:
                    self._enqueue(
                        AnalyticsEvent.MODEL_USAGE_DAILY_SUMMARY,
                        before,
                        {
                            "usage_date": row[0],
                            "role": row[1],
                            "mode": row[2],
                            "provider": row[3],
                            "model": row[4],
                            "use_count": int(row[5]),
                        },
                    )
                for properties in performance_summaries(performance_rows):
                    self._enqueue(
                        AnalyticsEvent.DICTATION_PERFORMANCE_DAILY_SUMMARY, before, properties
                    )
                self._execute("DELETE FROM daily_usage WHERE day < ?", (today,))
                self._execute("DELETE FROM daily_model_usage WHERE day < ?", (today,))
                self._execute("DELETE FROM daily_dictation_performance WHERE day < ?", (today,))

    # --- outbox -----------------------------------------------------------

    def ready_outbox(self, limit: int, moment: datetime) -> list[AnalyticsOutboxItem]:
        """Events ready to upload.

        Daily-activity events are withheld until their week has ended, so an
        upload cannot reveal which individual days the app was used.
        """
        week_start = self._week_start(moment)
        rows = self._query(
            "SELECT event_id, payload FROM outbox "
            "WHERE next_retry_at <= ? AND (event_name != ? OR created_at < ?) "
            "ORDER BY created_at LIMIT ?",
            (moment.timestamp(), AnalyticsEvent.ACTIVE_USER.value, week_start, limit),
        )
        return [AnalyticsOutboxItem(id=row[0], payload=bytes(row[1])) for row in rows]

    def acknowledge_uploaded(self, ids: Iterable[str], moment: datetime) -> None:
        identifiers = list(ids)
        if not identifiers:
            return
        with self._lock:
            with self._transaction():
                for identifier in identifiers:
                    self._execute("DELETE FROM outbox WHERE event_id = ?", (identifier,))
                cutoff = (moment - DEDUPE_RETENTION).timestamp()
                self._execute("DELETE FROM event_dedupe WHERE created_at < ?", (cutoff,))
            self._purge_deleted_pages()

    def retry(self, ids: Iterable[str], moment: datetime) -> None:
        """Exponential backoff, capped, so a failing endpoint cannot spin."""
        with self._lock, self._transaction():
            for identifier in ids:
                rows = self._query(
                    "SELECT attempt_count FROM outbox WHERE event_id = ?", (identifier,)
                )
                attempt = int(rows[0][0]) if rows else 0
                delay = min(3600.0, 30.0 * (2**attempt))
                self._execute(
                    "UPDATE outbox SET attempt_count = ?, next_retry_at = ? WHERE event_id = ?",
                    (attempt + 1, moment.timestamp() + delay, identifier),
                )

    def purge_detailed_analytics(self) -> None:
        """Opt-out: keep only daily activity and automatic performance summaries."""
        with self._lock:
            with self._transaction():
                self._execute(
                    "DELETE FROM outbox WHERE event_name != ? AND event_name != ?",
                    (
                        AnalyticsEvent.ACTIVE_USER.value,
                        AnalyticsEvent.DICTATION_PERFORMANCE_DAILY_SUMMARY.value,
                    ),
                )
                self._execute("DELETE FROM daily_usage")
                self._execute("DELETE FROM daily_model_usage")
                self._execute(
                    "DELETE FROM event_dedupe WHERE dedupe_key NOT LIKE ?", ("activity:%",)
                )
                self._execute("DELETE FROM onboarding_flows")
                self._execute("DELETE FROM model_download_attempts")
            self._purge_deleted_pages()

    def _purge_deleted_pages(self) -> None:
        try:
            self._execute("PRAGMA incremental_vacuum")
            self._execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except AnalyticsDatabaseError:
            pass


def _download_source(raw: str) -> ModelDownloadSource:
    try:
        return ModelDownloadSource(raw)
    except ValueError:
        return ModelDownloadSource.AUTOMATIC


def tryout_attempt_count_bucket(count: int) -> str:
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"


def tryout_duration_bucket(duration: float) -> str:
    """Half-second granularity below 30 s, then a single open-ended bucket."""
    duration = max(0.0, duration)
    if duration >= 30:
        return "30s_plus"
    upper_bound = max(0.5, math.ceil(duration * 2) / 2)
    if upper_bound == 0.5:
        return "500ms"
    whole_seconds = int(upper_bound)
    if upper_bound == whole_seconds:
        return f"{whole_seconds}s"
    return f"{whole_seconds}_5s"


def _quantile_bucket(counts: Sequence[int], percentile: float) -> str:
    target = max(1, math.ceil(sum(counts) * percentile))
    cumulative = 0
    for index, count in enumerate(counts):
        cumulative += count
        if cumulative >= target:
            if index >= len(PERFORMANCE_BUCKET_UPPER_BOUNDS):
                return "60000_plus"
            return str(PERFORMANCE_BUCKET_UPPER_BOUNDS[index])
    return "unknown"


def performance_summaries(rows: Sequence[tuple]) -> list[dict[str, Any]]:
    """Group raw histogram rows into one summary per day/version/OS."""
    bucket_count = len(PERFORMANCE_BUCKET_UPPER_BOUNDS) + 1
    summaries: dict[tuple[str, str, str], dict[str, list[int]]] = {}

    for row in rows:
        if len(row) != 6:
            continue
        day, app_version, os_version, metric, bucket_index, sample_count = row
        key = (day, app_version, os_version)
        metrics = summaries.setdefault(
            key, {"asr": [0] * bucket_count, "fluid_intelligence": [0] * bucket_count}
        )
        counts = metrics.get(metric)
        if counts is None:
            continue
        index = int(bucket_index)
        if 0 <= index < bucket_count:
            counts[index] += int(sample_count)

    results: list[dict[str, Any]] = []
    for key in sorted(summaries):
        day, app_version, os_version = key
        metrics = summaries[key]
        properties: dict[str, Any] = {
            "performance_date": day,
            "measured_app_version": app_version,
            "measured_os_version": os_version,
            "histogram_schema_version": 1,
        }
        for prefix in ("asr", "fluid_intelligence"):
            counts = metrics[prefix]
            sample_count = sum(counts)
            properties[f"{prefix}_sample_count"] = sample_count
            if sample_count == 0:
                continue
            properties[f"{prefix}_p50_bucket"] = _quantile_bucket(counts, 0.50)
            properties[f"{prefix}_p95_bucket"] = _quantile_bucket(counts, 0.95)
        results.append(properties)
    return results
