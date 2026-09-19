"""Concurrency primitives shared by the capture path.

Ports of `ThreadSafeAudioBuffer`, `AudioEngineRetirementDrain`,
`AudioCaptureReadinessGate` and `BoundedAudioHardwareQueue`.

These guard the shape of
problem: a PipeWire/PortAudio stream teardown can block while the server
re-negotiates a route, so teardown is serialized on its own thread, callers
can be released without aborting the native call, and a new stream is never
opened while the previous one is still unwinding.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence


class ThreadSafeAudioBuffer:
    """Float sample buffer shared between the capture thread and the ASR path."""

    def __init__(self) -> None:
        self._buffer: list[float] = []
        self._lock = threading.Lock()

    def append(self, new_samples: Sequence[float]) -> None:
        with self._lock:
            self._buffer.extend(new_samples)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def get_prefix(self, length: int) -> list[float]:
        with self._lock:
            safe_length = max(0, min(length, len(self._buffer)))
            return list(self._buffer[:safe_length])

    def get_range(self, start: int, count: int) -> list[float]:
        """An exact slice, or empty when the samples are not all available yet."""
        with self._lock:
            if start < 0 or count <= 0 or start > len(self._buffer):
                return []
            if count > len(self._buffer) - start:
                return []
            return list(self._buffer[start : start + count])

    def get_all(self) -> list[float]:
        with self._lock:
            return list(self._buffer)


class AudioEngineRetirementToken:
    """Holds the last reference to a stream while it waits to be torn down."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def release_engine(self) -> None:
        engine = self._engine
        self._engine = None
        close = getattr(engine, "close", None) or getattr(engine, "stop", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Teardown failures must never propagate into the capture path.
                pass


class AudioEngineRetirementDrain:
    """Serializes stream teardown and provides a completion barrier."""

    def __init__(self, name: str = "fluentry.audio-engine-retirement") -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._scheduled_release_count = 0
        self._work: list[Callable[[], None]] = []
        self._name = name
        self._worker: threading.Thread | None = None
        self._stopped = False

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._work and not self._stopped:
                    self._condition.wait()
                if self._stopped and not self._work:
                    return
                work = self._work.pop(0)
            try:
                work()
            except Exception:
                pass

    def schedule(self, token: AudioEngineRetirementToken) -> None:
        with self._condition:
            self._scheduled_release_count += 1
            self._ensure_worker()
            self._work.append(lambda: self._release(token))
            self._condition.notify_all()

    def release_and_wait(self, token: AudioEngineRetirementToken, timeout: float = 10.0) -> None:
        done = threading.Event()

        def work() -> None:
            self._release(token)
            done.set()

        with self._condition:
            self._scheduled_release_count += 1
            self._ensure_worker()
            self._work.append(work)
            self._condition.notify_all()
        done.wait(timeout)

    def wait_for_scheduled_releases(self, timeout: float = 10.0) -> None:
        """Barrier used at capture start.

        A fire-and-forget retirement must not overlap construction of the next
        stream, so start waits for everything already submitted.
        """
        with self._condition:
            if self._scheduled_release_count == 0:
                return
            done = threading.Event()
            self._ensure_worker()
            self._work.append(done.set)
            self._condition.notify_all()
        done.wait(timeout)

    def _release(self, token: AudioEngineRetirementToken) -> None:
        try:
            token.release_engine()
        finally:
            with self._condition:
                self._scheduled_release_count -= 1

    def shutdown(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=5)


class AudioCaptureReadinessResult(Enum):
    READY = "ready"
    CANCELLED = "cancelled"
    FORMAT_INVALIDATED = "formatInvalidated"
    TIMED_OUT = "timedOut"
    STALE_SESSION = "staleSession"


@dataclass(frozen=True)
class _ReadinessKey:
    session_id: int
    attempt_id: int


class AudioCaptureReadinessGate:
    """Waits for the first real PCM frame of a capture attempt.

    A capture is only "ready" once audio actually arrives — a stream can open
    successfully and then deliver nothing, which is exactly the failure this
    gate turns into a timeout instead of a silent recording.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: _ReadinessKey | None = None
        self._result: AudioCaptureReadinessResult | None = None
        self._event = threading.Event()

    def arm(self, session_id: int, attempt_id: int) -> None:
        with self._lock:
            had_waiter = self._key is not None and self._result is None
            self._key = _ReadinessKey(session_id, attempt_id)
            self._result = None
            previous_event = self._event
            self._event = threading.Event()
        if had_waiter:
            # Release anyone still waiting on the superseded attempt.
            previous_event.set()

    def wait(
        self, session_id: int, attempt_id: int, timeout: float
    ) -> AudioCaptureReadinessResult:
        key = _ReadinessKey(session_id, attempt_id)
        with self._lock:
            if self._key != key:
                return AudioCaptureReadinessResult.STALE_SESSION
            if self._result is not None:
                return self._result
            event = self._event

        if not event.wait(timeout):
            self._finish(key, AudioCaptureReadinessResult.TIMED_OUT)

        with self._lock:
            if self._key != key:
                return AudioCaptureReadinessResult.CANCELLED
            return self._result or AudioCaptureReadinessResult.CANCELLED

    def signal_first_pcm(self, session_id: int, attempt_id: int) -> None:
        self._finish(_ReadinessKey(session_id, attempt_id), AudioCaptureReadinessResult.READY)

    def cancel(self, session_id: int, attempt_id: int) -> None:
        self._finish(_ReadinessKey(session_id, attempt_id), AudioCaptureReadinessResult.CANCELLED)

    def signal_format_invalidation(self, session_id: int, attempt_id: int) -> None:
        self._finish(
            _ReadinessKey(session_id, attempt_id), AudioCaptureReadinessResult.FORMAT_INVALIDATED
        )

    def _finish(self, key: _ReadinessKey, result: AudioCaptureReadinessResult) -> None:
        with self._lock:
            if self._key != key or self._result is not None:
                return
            self._result = result
            event = self._event
        event.set()


class AudioHardwareFailure(Exception):
    pass


class HardwareTimedOut(AudioHardwareFailure):
    def __str__(self) -> str:
        return (
            "The microphone is not responding. Try again shortly. "
            "If it remains unavailable, quit and reopen Fluentry."
        )


class HardwareRecovering(HardwareTimedOut):
    pass


class HardwareDeviceStopped(HardwareTimedOut):
    pass


class HardwareCleanupFailed(AudioHardwareFailure):
    def __str__(self) -> str:
        return (
            "The microphone could not be reset safely. "
            "Quit and reopen Fluentry before recording again."
        )


class OperationCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True


@dataclass
class _PendingOperation:
    identifier: str
    event: threading.Event
    recover: Callable[[], bool]
    result: Any = None
    error: BaseException | None = None


class BoundedAudioHardwareQueue:
    """Serializes native audio operations and bounds how long callers wait.

    Cancelling releases the *caller*, never the native operation: interrupted
    hardware must finish its serialized cleanup before another operation is
    admitted, so a cancellation can never start a second capture stream on top
    of one that is still unwinding.
    """

    def __init__(self, timeout: float | None = None, name: str = "fluentry.audio-hardware") -> None:
        self._timeout = timeout
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingOperation] = {}
        self._recovering = False
        self._cleanup_failed = False
        self._availability = threading.Event()
        self._availability.set()
        self._name = name
        self._serial_lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        with self._lock:
            return not self._recovering and not self._cleanup_failed

    @property
    def is_recovering(self) -> bool:
        """True while serialized cleanup is still running."""
        with self._lock:
            return self._recovering

    @property
    def has_failed_cleanup(self) -> bool:
        """Latched until `clear_failure_after_serialized_recovery`."""
        with self._lock:
            return self._cleanup_failed

    def check_available(self) -> None:
        with self._lock:
            if self._cleanup_failed:
                raise HardwareCleanupFailed()
            if self._recovering:
                raise HardwareRecovering()

    def clear_failure_after_serialized_recovery(self) -> bool:
        """Only the serialized owner can declare an earlier failure resolved."""
        with self._lock:
            if self._recovering or not self._cleanup_failed:
                return False
            self._cleanup_failed = False
        self._resume_availability_waiters_if_ready()
        return True

    def wait_until_available(self, timeout: float | None = None) -> bool:
        """Wait for cleanup without starting another hardware operation."""
        with self._lock:
            if not self._recovering and not self._cleanup_failed:
                return True
            event = self._availability
            event.clear()
        return event.wait(timeout) and self.is_available

    def _resume_availability_waiters_if_ready(self) -> None:
        with self._lock:
            if self._recovering or self._cleanup_failed:
                return
        self._availability.set()

    def run(
        self,
        operation: Callable[[], Any],
        recover: Callable[[], bool],
        timeout: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Any:
        identifier = uuid.uuid4().hex
        with self._lock:
            if cancellation is not None and cancellation.is_cancelled:
                raise OperationCancelled()
            if self._cleanup_failed:
                raise HardwareCleanupFailed()
            if self._recovering:
                raise HardwareRecovering()
            pending = _PendingOperation(identifier, threading.Event(), recover)
            self._pending[identifier] = pending

        def work() -> None:
            # Bail out if the request was interrupted before the worker started.
            with self._lock:
                if identifier not in self._pending:
                    return
            try:
                value = operation()
                error: BaseException | None = None
            except BaseException as caught:  # surfaced to the caller below
                value = None
                error = caught
            with self._lock:
                request = self._pending.pop(identifier, None)
            if request is None:
                return  # Already cancelled or timed out.
            request.result = value
            request.error = error
            request.event.set()

        worker = threading.Thread(target=self._serialized(work), name=self._name, daemon=True)
        worker.start()

        deadline = timeout if timeout is not None else self._timeout
        if not pending.event.wait(deadline):
            self.interrupt(identifier, HardwareTimedOut())
            pending.event.wait(0)
        if pending.error is not None:
            raise pending.error
        if not pending.event.is_set():
            raise HardwareTimedOut()
        return pending.result

    def _serialized(self, work: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            with self._serial_lock:
                work()

        return run

    def cancel_pending_operations(self) -> None:
        """Used when the UI cancels a recording without cancelling its thread."""
        self.interrupt(None, OperationCancelled())

    def fail_pending_operations_after_device_stopped(self) -> None:
        """A device notification can establish failure before the call returns."""
        self.interrupt(None, HardwareDeviceStopped())

    def interrupt(self, request_id: str | None, error: BaseException) -> None:
        with self._lock:
            if self._recovering:
                return
            if request_id is not None:
                request = self._pending.get(request_id)
            else:
                request = next(iter(self._pending.values()), None)
            if request is None:
                return
            self._recovering = True
            self._availability.clear()
            requests = list(self._pending.values())
            self._pending.clear()
            recover = request.recover

        def recover_work() -> None:
            recovered = False
            try:
                recovered = bool(recover())
            finally:
                with self._lock:
                    self._cleanup_failed = not recovered
                    self._recovering = False
                if recovered:
                    self._resume_availability_waiters_if_ready()

        # The native call may still be running; recovery queues behind it
        # rather than tearing its resources down from this thread.
        threading.Thread(
            target=self._serialized(recover_work), name=f"{self._name}.recover", daemon=True
        ).start()

        for pending in requests:
            pending.error = error
            pending.event.set()
