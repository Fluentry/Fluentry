"""Pause whatever is playing while the user dictates, then put it back.

A port of `MediaPlaybackService` and `MediaPlaybackTransport`. macOS drove
this through a MediaRemote helper process; Linux uses MPRIS over D-Bus
(`playerctl`, or `gdbus` against `org.mpris.MediaPlayer2.*`). The snapshot
shape is unchanged — an application identifier, a process id, a title and a
playing flag — so the reconciler is identical.

The reconciler is the careful part, and none of it is platform-specific:

* the app only resumes playback it actually paused, and only when the same
  item is still paused,
* a slow query can never issue a pause after the hotkey was released,
* an unconfirmed command triggers a bounded cooldown rather than a retry
  storm against an unresponsive player,
* ownership of a confirmed pause survives a brief metadata outage instead of
  being dropped (or, worse, turning into a blind Play).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

#: How long the reconciler waits for a player to apply a command.
DEFAULT_SETTLE_SECONDS = 0.15
#: Cooldown after an unconfirmed command, bounding damage from hotkey spam.
COMMAND_COOLDOWN_SECONDS = 10.0

QUERY_TIMEOUT_SECONDS = 2.5
COMMAND_TIMEOUT_SECONDS = 1.0

#: Bounded read so a misbehaving helper cannot exhaust memory.
MAXIMUM_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class MediaPlaybackSnapshot:
    application_id: str
    process_id: int
    title: str | None
    is_playing: bool | None

    def matches(self, other: "MediaPlaybackSnapshot") -> bool:
        """Same player and same item.

        A browser exposes its media process, not a tab, so this can reject a
        changed player or item but cannot prove exact-tab identity.
        """
        return (
            self.application_id == other.application_id
            and self.process_id == other.process_id
            and self.title == other.title
        )


@dataclass(frozen=True)
class QueryUnavailable:
    reason: str


class MediaPlaybackCommand(str, Enum):
    PAUSE = "pause"
    PLAY = "play"


@dataclass(frozen=True)
class CommandCompleted:
    """The helper exited successfully. NOT acknowledgement from the player."""


@dataclass(frozen=True)
class CommandFailed:
    reason: str


class MediaPlaybackTransport(Protocol):
    def query(self) -> MediaPlaybackSnapshot | QueryUnavailable: ...

    def send(self, command: MediaPlaybackCommand) -> CommandCompleted | CommandFailed: ...


# --- transports -------------------------------------------------------------


def decode_playerctl_output(output: bytes | str) -> MediaPlaybackSnapshot | QueryUnavailable:
    """Parse the JSON line the metadata query prints."""
    if isinstance(output, bytes):
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError:
            return QueryUnavailable("invalid_utf8")
    else:
        text = output

    trimmed = text.strip()
    if not trimmed:
        return QueryUnavailable("empty_output")
    if trimmed in ("NIL", "null", "No players found"):
        return QueryUnavailable("no_media_reported")
    try:
        payload = json.loads(trimmed)
    except ValueError:
        return QueryUnavailable("invalid_payload")
    if not isinstance(payload, dict):
        return QueryUnavailable("invalid_payload")

    application_id = payload.get("applicationId") or payload.get("bundleIdentifier")
    if not isinstance(application_id, str) or not application_id:
        return QueryUnavailable("invalid_payload")

    raw_pid = payload.get("pid", payload.get("PID"))
    try:
        process_id = int(raw_pid)
    except (TypeError, ValueError):
        return QueryUnavailable("invalid_payload")

    title = payload.get("title")
    status = payload.get("status")
    if isinstance(status, str):
        lowered = status.strip().lower()
        is_playing = True if lowered == "playing" else (False if lowered in ("paused", "stopped") else None)
    elif isinstance(payload.get("isPlaying"), bool):
        is_playing = payload["isPlaying"]
    else:
        is_playing = None

    return MediaPlaybackSnapshot(
        application_id=application_id,
        process_id=process_id,
        title=title if isinstance(title, str) else None,
        is_playing=is_playing,
    )


class PlayerctlTransport:
    """MPRIS control through `playerctl`."""

    FORMAT = (
        '{{"applicationId": "{{playerName}}", "pid": 0, '
        '"title": "{{title}}", "status": "{{status}}"}}'
    )

    @staticmethod
    def is_available() -> bool:
        return shutil.which("playerctl") is not None

    def _invoke(self, arguments: list[str], timeout: float) -> tuple[bytes, str | None]:
        try:
            result = subprocess.run(
                ["playerctl", *arguments],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return b"", "timed_out"
        except OSError as error:
            return b"", f"helper_failed:{error}"
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            if "No players found" in stderr:
                return b"", "no_media_reported"
            return b"", "helper_failed"
        # Both pipes are drained by `run`, and the output is bounded here.
        return result.stdout[:MAXIMUM_OUTPUT_BYTES], None

    def query(self) -> MediaPlaybackSnapshot | QueryUnavailable:
        output, failure = self._invoke(
            ["metadata", "--format", self.FORMAT], QUERY_TIMEOUT_SECONDS
        )
        if failure is not None:
            return QueryUnavailable(failure)
        snapshot = decode_playerctl_output(output)
        if isinstance(snapshot, QueryUnavailable):
            return snapshot
        return snapshot

    def send(self, command: MediaPlaybackCommand) -> CommandCompleted | CommandFailed:
        _, failure = self._invoke([command.value], COMMAND_TIMEOUT_SECONDS)
        if failure is not None:
            return CommandFailed(failure)
        return CommandCompleted()


class ScriptedTransport:
    """Deterministic transport used by tests.

    A query or command can be *held* so a test can inject a state change at a
    precise point in the reconciler, which is how the ordering guarantees are
    pinned down without relying on thread scheduling.
    """

    def __init__(self, queries=None, commands=None) -> None:
        self.queries = list(queries or [])
        self.commands = list(commands or [])
        self.query_count = 0
        self.sent: list[MediaPlaybackCommand] = []
        self._held_query_index: int | None = None
        self._query_reached = threading.Event()
        self._query_released = threading.Event()
        self._held_result: object | None = None

    def hold_query(self, index: int) -> None:
        self._held_query_index = index

    def wait_for_query(self, timeout: float = 5.0) -> bool:
        return self._query_reached.wait(timeout)

    def release_query(self, result) -> None:
        self._held_result = result
        self._query_released.set()

    def query(self):
        self.query_count += 1
        if self._held_query_index is not None and self.query_count == self._held_query_index:
            self._query_reached.set()
            self._query_released.wait(5)
            return self._held_result
        if self.queries:
            return self.queries.pop(0)
        return QueryUnavailable("no_media_reported")

    def send(self, command: MediaPlaybackCommand):
        self.sent.append(command)
        if self.commands:
            return self.commands.pop(0)
        return CommandCompleted()


def make_media_transport() -> MediaPlaybackTransport | None:
    if PlayerctlTransport.is_available():
        return PlayerctlTransport()
    return None


# --- reconciler -------------------------------------------------------------


@dataclass
class _Session:
    id: int
    may_pause: bool


class MediaPlaybackService:
    def __init__(
        self,
        transport: MediaPlaybackTransport,
        settle: Callable[[], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.transport = transport
        self._settle = settle or (lambda: time.sleep(DEFAULT_SETTLE_SECONDS))
        self._now = now
        self._log = log or (lambda message: None)

        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._revision = 0
        self._attempted_session: int | None = None
        self._paused_target: MediaPlaybackSnapshot | None = None
        self._worker: threading.Thread | None = None
        self._suspended_until = 0.0
        self._is_shutting_down = False

    # --- intent (synchronous; never waits for media I/O) -------------------

    def recording_started(self, session_id: int, enabled: bool) -> None:
        with self._lock:
            if self._is_shutting_down:
                return
            self._session = _Session(session_id, may_pause=True) if enabled else None
            self._revision += 1
        self._log(f"recording_started session={session_id} enabled={enabled}")
        self._wake()

    def recording_stopped(self, session_id: int) -> None:
        """Stops a slow query from pausing after the hotkey was released.

        A previously confirmed pause stays owned until transcription finishes.
        """
        with self._lock:
            if self._session is None or self._session.id != session_id:
                return
            self._session.may_pause = False
            self._revision += 1
        self._log(f"recording_stopped session={session_id}")
        self._wake()

    def session_finished(self, session_id: int) -> None:
        """An older transcription finishing cannot resume a newer recording's media."""
        with self._lock:
            if self._session is None or self._session.id != session_id:
                return
            self._session = None
            self._revision += 1
        self._log(f"session_finished session={session_id}")
        self._wake()

    def begin_shutdown(self) -> None:
        with self._lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True
            self._session = None
            self._revision += 1
        self._wake()

    def shutdown(self, timeout: float = 10.0) -> None:
        self.begin_shutdown()
        self.wait_until_settled(timeout)

    def wait_until_settled(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                worker = self._worker
            if worker is None:
                return
            worker.join(max(0.0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                return

    @property
    def owns_paused_media(self) -> bool:
        with self._lock:
            return self._paused_target is not None

    # --- reconciliation ----------------------------------------------------

    def _wake(self) -> None:
        with self._lock:
            if self._worker is not None:
                return
            worker = threading.Thread(
                target=self._reconcile, name="fluentry.media", daemon=True
            )
            self._worker = worker
        worker.start()

    def _reconcile(self) -> None:
        while True:
            with self._lock:
                observed_revision = self._revision
                session = self._session
                target = self._paused_target
                attempted = self._attempted_session

            if session is not None:
                if session.may_pause and attempted != session.id:
                    with self._lock:
                        self._attempted_session = session.id
                    self._pause(session.id)
            elif target is not None:
                self._resume(target)

            with self._lock:
                if observed_revision == self._revision:
                    self._worker = None
                    return

    def _can_pause(self, session_id: int) -> bool:
        with self._lock:
            return self._session is not None and self._session.id == session_id and self._session.may_pause

    def _pause(self, session_id: int) -> None:
        with self._lock:
            suspended_until = self._suspended_until
        if self._now() < suspended_until:
            self._log(f"pause_suppressed session={session_id} reason=player_backoff")
            return

        before = self._query_before_pause(session_id)
        if before is None:
            return
        if not self._can_pause(session_id):
            self._log(f"pause_skipped session={session_id} reason=stale_recording")
            return

        with self._lock:
            owned = self._paused_target
        if owned is not None and owned.matches(before) and before.is_playing is False:
            self._log(f"pause_retained session={session_id} reason=already_owned")
            return

        # A player change or manual playback invalidates our previous ownership.
        with self._lock:
            self._paused_target = None
        if before.is_playing is not True:
            self._log(f"pause_skipped session={session_id} reason=not_known_playing")
            return

        result = self.transport.send(MediaPlaybackCommand.PAUSE)
        self._log_command(MediaPlaybackCommand.PAUSE, result, session_id)
        # Even a timed-out helper may have delivered its command, so observe
        # the player before deciding whether we own a pause to restore.
        paused = self._verify(before, playing=False, context=f"pause session={session_id}")
        if paused is not None:
            with self._lock:
                self._paused_target = paused
            self._log(f"pause_verified session={session_id}")
        else:
            self._back_off(f"pause session={session_id}")

    def _query_before_pause(self, session_id: int) -> MediaPlaybackSnapshot | None:
        for attempt in range(1, 4):
            if not self._can_pause(session_id):
                return None
            snapshot = self._query(f"before_pause session={session_id} attempt={attempt}")
            if snapshot is not None:
                return snapshot
            if not self._can_pause(session_id):
                return None
            if attempt < 3:
                self._settle()
        return None

    def _resume(self, target: MediaPlaybackSnapshot) -> None:
        for attempt in range(1, 3):
            before = self._query_before_resume()
            if before is None:
                with self._lock:
                    if self._session is not None:
                        return
                    shutting_down = self._is_shutting_down
                # Missing metadata is not evidence that our confirmed pause
                # ended. Allow one more bounded read cycle; never issue Play
                # without a matching paused item, and keep ownership if the
                # outage outlasts this recovery window.
                if not shutting_down and attempt < 2:
                    self._settle()
                    continue
                self._log("resume_deferred reason=unknown_player ownership=retained")
                return

            with self._lock:
                if self._session is not None:
                    self._log("resume_skipped reason=new_recording")
                    return
            if not target.matches(before) or before.is_playing is not False:
                with self._lock:
                    self._paused_target = None
                self._log("resume_skipped reason=player_item_or_state_changed")
                return

            result = self.transport.send(MediaPlaybackCommand.PLAY)
            self._log_command(MediaPlaybackCommand.PLAY, result, None)
            if self._verify(before, playing=True, context="resume") is not None:
                with self._lock:
                    self._paused_target = None
                self._log("resume_verified")
                return

            with self._lock:
                if self._session is not None:
                    return
                shutting_down = self._is_shutting_down
            # Quit is best-effort within the app's termination budget: no extra
            # command cycle once shutdown has begun.
            if shutting_down or attempt == 2:
                with self._lock:
                    self._paused_target = None
                self._back_off("resume")
                return

    def _query_before_resume(self) -> MediaPlaybackSnapshot | None:
        # Retry reads, never playback commands; give up after three calls.
        for attempt in range(1, 4):
            with self._lock:
                if self._session is not None:
                    return None
            snapshot = self._query(f"before_resume attempt={attempt}")
            if snapshot is not None:
                return snapshot
            with self._lock:
                if self._session is not None:
                    return None
                if self._is_shutting_down:
                    return None
            if attempt < 3:
                self._settle()
        return None

    def _verify(
        self, target: MediaPlaybackSnapshot, playing: bool, context: str
    ) -> MediaPlaybackSnapshot | None:
        # Read at most twice; never blindly retry a playback command. This
        # checks reported state, not rendered video.
        for attempt in range(1, 3):
            with self._lock:
                shutting_down = self._is_shutting_down
            if attempt > 1 and shutting_down:
                break
            self._settle()
            observed = self._query(f"verify_{context} attempt={attempt}")
            if observed is None:
                continue
            if not target.matches(observed):
                self._log(f"verification_failed context={context} reason=player_or_item_changed")
                return None
            if observed.is_playing == playing:
                return observed
        self._log(f"verification_failed context={context} reason=state_not_confirmed")
        return None

    def _query(self, context: str) -> MediaPlaybackSnapshot | None:
        started = self._now()
        result = self.transport.query()
        elapsed = int((self._now() - started) * 1000)
        if isinstance(result, MediaPlaybackSnapshot):
            self._log(
                f"query context={context} elapsedMs={elapsed} "
                f"app={result.application_id} pid={result.process_id} "
                f"playing={result.is_playing if result.is_playing is not None else 'unknown'} "
                f"hasTitle={result.title is not None}"
            )
            return result
        self._log(f"query_unavailable context={context} elapsedMs={elapsed} reason={result.reason}")
        return None

    def _back_off(self, context: str) -> None:
        with self._lock:
            self._suspended_until = self._now() + COMMAND_COOLDOWN_SECONDS
        self._log(f"commands_suspended context={context} seconds={int(COMMAND_COOLDOWN_SECONDS)}")

    def _log_command(self, command: MediaPlaybackCommand, result, session_id: int | None) -> None:
        if isinstance(result, CommandCompleted):
            status = "helper_completed_player_unconfirmed"
        else:
            status = f"failed:{result.reason}"
        session = str(session_id) if session_id is not None else "none"
        self._log(f"command={command.value} session={session} result={status}")
