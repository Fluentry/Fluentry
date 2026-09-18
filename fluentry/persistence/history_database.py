"""SQLite storage for transcription history.

Same schema and guarantees as the macOS build: one JSON payload per entry (not
one blob for the whole history), so a single dictation writes a single row; a
failed write rolls back so a failed replacement can never leave history empty.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .defaults import Defaults, data_home
from .history_entry import TranscriptionHistoryEntry

LEGACY_DEFAULTS_KEY = "TranscriptionHistoryEntries"


@dataclass(frozen=True)
class Record:
    id: str
    payload: bytes


class HistoryDatabaseError(Exception):
    pass


class TranscriptionHistoryDatabase:
    """Owned by the writer's serial thread."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        # Manual transaction control so the explicit BEGIN IMMEDIATE below is
        # the only transaction; sqlite3's implicit one would nest and fail.
        self._connection.isolation_level = None
        try:
            self._execute("PRAGMA busy_timeout=2000")
            self._execute("PRAGMA journal_mode=WAL")
            self._execute("PRAGMA synchronous=FULL")
            self._execute("PRAGMA secure_delete=ON")
            self._execute("CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
            self._execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY)")
        except Exception:
            self._connection.close()
            raise

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            pass

    def _execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, parameters)
        except sqlite3.Error as error:
            raise HistoryDatabaseError(str(error)) from error

    @property
    def is_migrated(self) -> bool:
        cursor = self._execute("SELECT 1 FROM metadata WHERE key='legacy_imported'")
        return cursor.fetchone() is not None

    def migrate(self, records: Iterable[Record]) -> None:
        if self.is_migrated:
            return
        with self._transaction():
            for record in records:
                self._upsert(record)
            self._execute("INSERT INTO metadata(key) VALUES ('legacy_imported')")

    def read(self) -> list[Record]:
        cursor = self._execute("SELECT id, payload FROM history")
        return [Record(id=row[0], payload=bytes(row[1])) for row in cursor.fetchall()]

    def write(self, upserts: Iterable[Record], deletes: Iterable[str], replacing: bool) -> None:
        with self._transaction():
            if replacing:
                self._execute("DELETE FROM history")
            for identifier in deletes:
                self._execute("DELETE FROM history WHERE id=?", (identifier,))
            for record in upserts:
                self._upsert(record)

    def _upsert(self, record: Record) -> None:
        self._execute(
            "INSERT OR REPLACE INTO history(id,payload) VALUES (?,?)",
            (record.id, sqlite3.Binary(record.payload)),
        )

    class _Transaction:
        def __init__(self, database: "TranscriptionHistoryDatabase") -> None:
            self.database = database

        def __enter__(self) -> None:
            self.database._execute("BEGIN IMMEDIATE")

        def __exit__(self, exc_type, exc, traceback) -> bool:
            if exc_type is None:
                self.database._execute("COMMIT")
                return False
            try:
                self.database._execute("ROLLBACK")
            except HistoryDatabaseError:
                pass
            return False

    def _transaction(self) -> "_Transaction":
        return TranscriptionHistoryDatabase._Transaction(self)


def default_history_path() -> Path:
    return data_home() / "TranscriptionHistory.sqlite3"


class TranscriptionHistoryWriter:
    """All disk and JSON work happens on one serial worker thread."""

    def __init__(self, defaults: Defaults | None = None, path: Path | None = None) -> None:
        self.defaults = defaults if defaults is not None else Defaults()
        self.path = path or default_history_path()
        self._database: TranscriptionHistoryDatabase | None = None
        self._write_error: Exception | None = None
        self._queue: "queue.Queue[Callable[[], None] | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="fluid.history.persistence", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            work = self._queue.get()
            if work is None:
                return
            try:
                work()
            except Exception:
                pass

    def shutdown(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)
        if self._database is not None:
            self._database.close()
            self._database = None

    def _submit(self, work: Callable[[], None]) -> threading.Event:
        done = threading.Event()

        def wrapper() -> None:
            try:
                work()
            finally:
                done.set()

        self._queue.put(wrapper)
        return done

    def load(self, timeout: float = 30.0) -> list[TranscriptionHistoryEntry]:
        result: dict[str, object] = {}

        def work() -> None:
            try:
                database = self._database or TranscriptionHistoryDatabase(self.path)
                self._database = database
                if not database.is_migrated:
                    legacy_payload = self.defaults.object(LEGACY_DEFAULTS_KEY)
                    legacy: list[TranscriptionHistoryEntry] = []
                    if legacy_payload is not None:
                        decoded = (
                            json.loads(legacy_payload)
                            if isinstance(legacy_payload, (str, bytes))
                            else legacy_payload
                        )
                        if not isinstance(decoded, list):
                            raise HistoryDatabaseError("Legacy history payload is not a list")
                        legacy = [TranscriptionHistoryEntry.from_dict(item) for item in decoded]
                    database.migrate([_record(entry) for entry in legacy])
                entries = [
                    TranscriptionHistoryEntry.from_dict(json.loads(record.payload.decode("utf-8")))
                    for record in database.read()
                ]
                entries.sort(key=lambda entry: entry.timestamp, reverse=True)
                # The transaction is committed and every payload decoded before
                # retiring legacy storage.
                self.defaults.remove(LEGACY_DEFAULTS_KEY)
                result["entries"] = entries
            except Exception as error:  # surfaced to the caller below
                result["error"] = error

        self._submit(work).wait(timeout)
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        return result.get("entries", [])  # type: ignore[return-value]

    def write(
        self,
        upserts: list[TranscriptionHistoryEntry],
        deletes: list[str] | None = None,
        replacing: bool = False,
        completion: Callable[[Exception | None], None] | None = None,
    ) -> None:
        deletes = deletes or []

        def work() -> None:
            try:
                if self._database is None:
                    raise HistoryDatabaseError("History is not loaded.")
                records = [_record(entry) for entry in upserts]
                self._database.write(records, deletes, replacing)
                if replacing:
                    self._write_error = None
                if completion:
                    completion(None)
            except Exception as error:
                self._write_error = error
                if completion:
                    completion(error)

        self._queue.put(work)

    def drain(self, timeout: float = 30.0) -> Exception | None:
        holder: dict[str, Exception | None] = {}

        def work() -> None:
            holder["error"] = self._write_error

        self._submit(work).wait(timeout)
        return holder.get("error")


def _record(entry: TranscriptionHistoryEntry) -> Record:
    return Record(
        id=entry.id,
        payload=json.dumps(entry.to_dict(), separators=(",", ":")).encode("utf-8"),
    )
