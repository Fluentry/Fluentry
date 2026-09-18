"""Capture idle/recovery policy.

A port of `AudioCaptureIdlePolicy`. Every rule here is about one problem:
audio hardware changes underneath a running capture — a Bluetooth headset
finishes connecting, a dock is attached, the kernel re-enumerates a USB
interface — and the app must decide whether to retry, prewarm, reconcile the
selected input, or do nothing.

The rules are pure functions over device snapshots, so they behave identically
whether the snapshots come from PipeWire, PulseAudio, or a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from .audio_device import AudioDeviceInfo


@dataclass(frozen=True)
class CaptureAttemptIdentity:
    uid: str
    name: str | None
    is_bluetooth: bool
    is_internal_microphone: bool

    @staticmethod
    def resolve(
        selected_input: AudioDeviceInfo | None,
        forcing_input_uid: str | None,
        previous: "CaptureAttemptIdentity | None",
    ) -> "CaptureAttemptIdentity | None":
        uid = forcing_input_uid or (selected_input.uid if selected_input else None)
        if uid is None:
            return None
        if selected_input is not None and selected_input.uid == uid:
            return CaptureAttemptIdentity(
                uid=uid,
                name=selected_input.name,
                is_bluetooth=selected_input.is_bluetooth,
                is_internal_microphone=selected_input.is_unavailable_when_lid_closed,
            )
        # A forced device that has momentarily disappeared keeps the identity we
        # already had, so a settling Bluetooth route is not misread as a new one.
        if previous is not None and previous.uid == uid:
            return previous
        return CaptureAttemptIdentity(uid=uid, name=None, is_bluetooth=False, is_internal_microphone=False)


class BluetoothStartupRouteChangeDisposition(Enum):
    RETRY_CURRENT_START = "retryCurrentStart"
    PRESERVE_DEFERRED_WORK = "preserveDeferredWork"
    IGNORE = "ignore"


@dataclass(frozen=True)
class DeferredRouteRecoveryRequest:
    reason: str
    requires_idle_prewarm: bool
    reconciles_input_selection: bool


@dataclass
class DeferredBluetoothRouteRecovery:
    """Holds route work that arrived mid-start, to run once the start settles."""

    _request: DeferredRouteRecoveryRequest | None = None

    def preserve(
        self, reason: str, requires_idle_prewarm: bool, reconciles_input_selection: bool
    ) -> None:
        if not requires_idle_prewarm and not reconciles_input_selection:
            return
        existing = self._request
        self._request = DeferredRouteRecoveryRequest(
            reason=existing.reason if existing else reason,
            requires_idle_prewarm=requires_idle_prewarm or (existing.requires_idle_prewarm if existing else False),
            reconciles_input_selection=reconciles_input_selection
            or (existing.reconciles_input_selection if existing else False),
        )

    def take(self) -> DeferredRouteRecoveryRequest | None:
        request = self._request
        self._request = None
        return request


@dataclass
class SilentPCMRecoveryWatchdog:
    """Recovers an internal mic that streams digital silence after a route change.

    Only fires once, and only after real signal has been seen, so a genuinely
    quiet room never triggers a restart.
    """

    REQUIRED_SILENT_WINDOWS = 3
    MAXIMUM_SILENT_RMS = 0.00_001
    MAXIMUM_SILENT_PEAK = 0.00_005

    _has_seen_signal: bool = False
    _consecutive_silent_windows: int = 0
    _has_requested_recovery: bool = False

    def should_recover(
        self,
        is_internal_microphone: bool,
        is_direct_capture: bool,
        rms: float,
        peak: float,
    ) -> bool:
        if not is_internal_microphone or not is_direct_capture or self._has_requested_recovery:
            return False
        is_effectively_silent = rms <= self.MAXIMUM_SILENT_RMS and peak <= self.MAXIMUM_SILENT_PEAK
        if not is_effectively_silent:
            self._has_seen_signal = True
            self._consecutive_silent_windows = 0
            return False
        if not self._has_seen_signal:
            return False
        self._consecutive_silent_windows += 1
        if self._consecutive_silent_windows < self.REQUIRED_SILENT_WINDOWS:
            return False
        self._has_requested_recovery = True
        return True


@dataclass
class BluetoothInputStabilization:
    """Admits same-device retries while a Bluetooth route is still settling.

    An admitted attempt keeps its normal readiness timeout, so a nearly-settled
    route is not cancelled at the deadline.
    """

    RETRY_ADMISSION_WINDOW = 5.0

    input_uid: str | None = None
    started_at: float | None = None

    def should_retry(self, input_uid: str, is_bluetooth_input: bool, now: float) -> bool:
        if not is_bluetooth_input and self.input_uid != input_uid:
            return False
        if self.input_uid != input_uid or self.started_at is None:
            self.input_uid = input_uid
            self.started_at = now
        return self.elapsed(now) < self.RETRY_ADMISSION_WINDOW

    def elapsed(self, now: float) -> float:
        if self.started_at is None:
            return 0.0
        return max(now - self.started_at, 0.0)


def bluetooth_input_awaiting_availability(
    priority_input_uids: Sequence[str],
    preferred_input_uid: str | None,
    resolved_input_uid: str | None,
    all_devices: Sequence[AudioDeviceInfo],
    excluding: Iterable[str] = (),
) -> AudioDeviceInfo | None:
    """A higher-priority Bluetooth device that is present but has no input yet."""
    excluded = set(excluding)

    def settling(uid: str) -> AudioDeviceInfo | None:
        candidate = next((device for device in all_devices if device.uid == uid), None)
        if candidate is None:
            return None
        if candidate.is_bluetooth and not candidate.has_input and candidate.is_alive:
            return candidate
        return None

    for uid in priority_input_uids:
        if uid in excluded:
            continue
        if uid == resolved_input_uid:
            return None
        candidate = settling(uid)
        if candidate is not None:
            return candidate

    if preferred_input_uid is None:
        return None
    if preferred_input_uid in excluded or preferred_input_uid == resolved_input_uid:
        return None
    return settling(preferred_input_uid)


def should_prewarm_capture(direct_audio_capture_enabled: bool) -> bool:
    return direct_audio_capture_enabled


def did_resolved_priority_input_change(
    priority_input_uids: Sequence[str],
    previous_input_uids: set[str],
    current_input_uids: set[str],
) -> bool:
    previous_choice = next((uid for uid in priority_input_uids if uid in previous_input_uids), None)
    current_choice = next((uid for uid in priority_input_uids if uid in current_input_uids), None)
    return previous_choice != current_choice


def did_resolved_priority_input_identity_change(
    priority_input_uids: Sequence[str],
    previous_input_device_ids_by_uid: dict[str, int],
    current_input_device_ids_by_uid: dict[str, int],
) -> bool:
    """True when the chosen device changed, or kept its name but got a new id.

    A USB interface that is unplugged and replugged keeps its node name but
    gets a fresh object id; the capture must follow the new one.
    """
    previous_choice = next(
        (uid for uid in priority_input_uids if uid in previous_input_device_ids_by_uid), None
    )
    current_choice = next(
        (uid for uid in priority_input_uids if uid in current_input_device_ids_by_uid), None
    )
    if previous_choice != current_choice:
        return True
    if current_choice is None:
        return False
    return previous_input_device_ids_by_uid.get(current_choice) != current_input_device_ids_by_uid.get(
        current_choice
    )


def should_reconcile_input_selection(
    priority_input_uids: Sequence[str],
    migration_pending: bool,
    previous_input_uids: set[str],
    current_input_uids: set[str],
) -> bool:
    if not current_input_uids:
        # Everything went away: only worth reconciling if we had something.
        return bool(previous_input_uids) and did_resolved_priority_input_change(
            priority_input_uids, previous_input_uids, current_input_uids
        )
    return (
        migration_pending
        or not priority_input_uids
        or did_resolved_priority_input_change(
            priority_input_uids, previous_input_uids, current_input_uids
        )
    )


def should_recover_engine_configuration_change(is_running: bool, is_starting: bool) -> bool:
    """Only a capture in flight cares about a graph reconfiguration."""
    return is_running or is_starting


def should_defer_route_recovery_to_bluetooth_start(
    direct_capture_enabled: bool,
    is_starting: bool,
    is_running: bool,
    attempted_input_is_bluetooth: bool,
) -> bool:
    return direct_capture_enabled and is_starting and not is_running and attempted_input_is_bluetooth


def bluetooth_startup_route_change_disposition(
    invalidates_current_start: bool,
    requires_idle_prewarm: bool,
    reconciles_input_selection: bool,
) -> BluetoothStartupRouteChangeDisposition:
    if invalidates_current_start:
        return BluetoothStartupRouteChangeDisposition.RETRY_CURRENT_START
    if requires_idle_prewarm or reconciles_input_selection:
        return BluetoothStartupRouteChangeDisposition.PRESERVE_DEFERRED_WORK
    return BluetoothStartupRouteChangeDisposition.IGNORE


def should_recover_after_deferred_bluetooth_reconciliation(
    is_running: bool,
    confirmed_input_uid: str | None,
    active_device_id: int | None,
    resolved_input_uid: str | None,
    resolved_device_id: int | None,
    has_prepared_capture: bool,
    requires_idle_prewarm: bool,
) -> bool:
    if is_running:
        return resolved_input_uid != confirmed_input_uid or resolved_device_id != active_device_id
    if has_prepared_capture:
        return resolved_device_id != active_device_id
    return requires_idle_prewarm
