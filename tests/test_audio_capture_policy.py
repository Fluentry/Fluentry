"""Port of the capture idle/recovery policy coverage in AudioHardwareRecoveryTests."""

import pytest

from fluentry.services.audio_device import AudioDeviceInfo, TransportType
from fluentry.services.audio_capture_policy import (
    BluetoothInputStabilization,
    BluetoothStartupRouteChangeDisposition,
    CaptureAttemptIdentity,
    DeferredBluetoothRouteRecovery,
    SilentPCMRecoveryWatchdog,
    bluetooth_input_awaiting_availability,
    bluetooth_startup_route_change_disposition,
    did_resolved_priority_input_change,
    did_resolved_priority_input_identity_change,
    should_defer_route_recovery_to_bluetooth_start,
    should_prewarm_capture,
    should_recover_after_deferred_bluetooth_reconciliation,
    should_recover_engine_configuration_change,
    should_reconcile_input_selection,
)

REQUIRED_SILENT_WINDOWS = SilentPCMRecoveryWatchdog.REQUIRED_SILENT_WINDOWS


def device(uid, name, transport=TransportType.USB, has_input=True, identifier=1, port=None):
    return AudioDeviceInfo(
        id=identifier,
        uid=uid,
        name=name,
        has_input=has_input,
        has_output=not has_input,
        transport_type=transport,
        input_port_id=port,
    )


# --- Bluetooth stabilization ------------------------------------------------


def test_bluetooth_startup_admits_same_input_retries_within_five_second_window():
    stabilization = BluetoothInputStabilization()

    assert stabilization.should_retry("airpods", is_bluetooth_input=True, now=10)
    assert stabilization.should_retry("airpods", is_bluetooth_input=False, now=14.999)
    assert not stabilization.should_retry("airpods", is_bluetooth_input=False, now=15)


def test_bluetooth_startup_policy_does_not_affect_other_inputs():
    stabilization = BluetoothInputStabilization()
    assert not stabilization.should_retry("usb", is_bluetooth_input=False, now=10)


def test_switching_device_restarts_the_admission_window():
    stabilization = BluetoothInputStabilization()
    assert stabilization.should_retry("airpods", is_bluetooth_input=True, now=10)
    assert stabilization.should_retry("buds", is_bluetooth_input=True, now=20)
    assert stabilization.elapsed(21) == pytest.approx(1.0)


# --- capture attempt identity -----------------------------------------------


def test_capture_attempt_retains_bluetooth_identity_when_forced_device_disappears():
    airpods = device("airpods", "AirPods Microphone", TransportType.BLUETOOTH)
    selected = CaptureAttemptIdentity.resolve(airpods, forcing_input_uid=None, previous=None)
    retry = CaptureAttemptIdentity.resolve(None, forcing_input_uid="airpods", previous=selected)

    assert retry == selected
    assert retry.is_bluetooth


def test_capture_attempt_does_not_transfer_bluetooth_identity_to_a_different_device():
    previous = CaptureAttemptIdentity("airpods", "AirPods Microphone", True, False)
    replacement = CaptureAttemptIdentity.resolve(None, forcing_input_uid="usb-mic", previous=previous)

    assert replacement.uid == "usb-mic"
    assert not replacement.is_bluetooth


def test_capture_attempt_identity_is_none_without_a_device():
    assert CaptureAttemptIdentity.resolve(None, forcing_input_uid=None, previous=None) is None


def test_capture_attempt_identity_marks_the_internal_microphone():
    internal = device("internal", "Built-in", TransportType.BUILT_IN, port="analog-input-internal-mic")
    identity = CaptureAttemptIdentity.resolve(internal, None, None)
    assert identity.is_internal_microphone


# --- settling Bluetooth inputs ----------------------------------------------


def airpods_output_only():
    return device("airpods", "AirPods", TransportType.BLUETOOTH, has_input=False, identifier=42)


def test_capture_attempt_seeds_preferred_bluetooth_identity_before_input_appears():
    candidate = bluetooth_input_awaiting_availability(
        priority_input_uids=["airpods", "built-in"],
        preferred_input_uid="airpods",
        resolved_input_uid="built-in",
        all_devices=[airpods_output_only()],
    )
    assert candidate.uid == "airpods"
    assert candidate.is_bluetooth


def test_capture_attempt_does_not_wait_for_a_lower_priority_bluetooth_input():
    built_in = device("built-in", "Built-in Microphone", TransportType.BUILT_IN)
    candidate = bluetooth_input_awaiting_availability(
        priority_input_uids=["built-in", "airpods"],
        preferred_input_uid="built-in",
        resolved_input_uid="built-in",
        all_devices=[built_in, airpods_output_only()],
    )
    assert candidate is None


def test_capture_attempt_skips_disconnected_priority_before_settling_bluetooth_input():
    candidate = bluetooth_input_awaiting_availability(
        priority_input_uids=["disconnected-usb", "airpods", "built-in"],
        preferred_input_uid="disconnected-usb",
        resolved_input_uid="built-in",
        all_devices=[airpods_output_only()],
    )
    assert candidate.uid == "airpods"


def test_excluded_devices_are_never_waited_for():
    candidate = bluetooth_input_awaiting_availability(
        priority_input_uids=["airpods"],
        preferred_input_uid="airpods",
        resolved_input_uid="built-in",
        all_devices=[airpods_output_only()],
        excluding=["airpods"],
    )
    assert candidate is None


def test_a_device_that_already_has_input_is_not_settling():
    ready = device("airpods", "AirPods", TransportType.BLUETOOTH, has_input=True)
    candidate = bluetooth_input_awaiting_availability(
        priority_input_uids=["airpods"],
        preferred_input_uid="airpods",
        resolved_input_uid="built-in",
        all_devices=[ready],
    )
    assert candidate is None


# --- deferred route recovery ------------------------------------------------


def test_bluetooth_startup_defers_only_a_starting_bluetooth_attempt():
    assert should_defer_route_recovery_to_bluetooth_start(
        direct_capture_enabled=True, is_starting=True, is_running=False, attempted_input_is_bluetooth=True
    )
    assert not should_defer_route_recovery_to_bluetooth_start(
        direct_capture_enabled=True, is_starting=True, is_running=True, attempted_input_is_bluetooth=True
    )
    assert not should_defer_route_recovery_to_bluetooth_start(
        direct_capture_enabled=True, is_starting=True, is_running=False, attempted_input_is_bluetooth=False
    )
    assert not should_defer_route_recovery_to_bluetooth_start(
        direct_capture_enabled=False, is_starting=True, is_running=False, attempted_input_is_bluetooth=True
    )


def test_bluetooth_startup_route_change_disposition():
    assert (
        bluetooth_startup_route_change_disposition(True, True, False)
        is BluetoothStartupRouteChangeDisposition.RETRY_CURRENT_START
    )
    assert (
        bluetooth_startup_route_change_disposition(False, True, True)
        is BluetoothStartupRouteChangeDisposition.PRESERVE_DEFERRED_WORK
    )
    assert (
        bluetooth_startup_route_change_disposition(False, False, False)
        is BluetoothStartupRouteChangeDisposition.IGNORE
    )


def test_bluetooth_startup_preserves_only_explicit_reconciliation_work():
    deferred = DeferredBluetoothRouteRecovery()

    deferred.preserve("ordinary route churn", requires_idle_prewarm=False, reconciles_input_selection=False)
    assert deferred.take() is None

    deferred.preserve("settings backup restored", requires_idle_prewarm=True, reconciles_input_selection=False)
    deferred.preserve("input topology changed", requires_idle_prewarm=False, reconciles_input_selection=True)
    request = deferred.take()

    assert request.reason == "settings backup restored"
    assert request.requires_idle_prewarm is True
    assert request.reconciles_input_selection is True
    assert deferred.take() is None


def test_deferred_reconciliation_leaves_a_matching_active_input_untouched():
    assert not should_recover_after_deferred_bluetooth_reconciliation(
        is_running=True,
        confirmed_input_uid="airpods",
        active_device_id=42,
        resolved_input_uid="airpods",
        resolved_device_id=42,
        has_prepared_capture=True,
        requires_idle_prewarm=True,
    )


def test_deferred_reconciliation_recovers_changed_selection_or_identity():
    assert should_recover_after_deferred_bluetooth_reconciliation(
        is_running=True,
        confirmed_input_uid="airpods",
        active_device_id=42,
        resolved_input_uid="usb",
        resolved_device_id=88,
        has_prepared_capture=True,
        requires_idle_prewarm=True,
    )
    assert should_recover_after_deferred_bluetooth_reconciliation(
        is_running=True,
        confirmed_input_uid="airpods",
        active_device_id=42,
        resolved_input_uid="airpods",
        resolved_device_id=43,
        has_prepared_capture=True,
        requires_idle_prewarm=True,
    )


def test_deferred_reconciliation_preserves_idle_prewarm_intent():
    assert not should_recover_after_deferred_bluetooth_reconciliation(
        is_running=False,
        confirmed_input_uid=None,
        active_device_id=42,
        resolved_input_uid="airpods",
        resolved_device_id=42,
        has_prepared_capture=True,
        requires_idle_prewarm=True,
    )
    assert should_recover_after_deferred_bluetooth_reconciliation(
        is_running=False,
        confirmed_input_uid=None,
        active_device_id=None,
        resolved_input_uid="airpods",
        resolved_device_id=42,
        has_prepared_capture=False,
        requires_idle_prewarm=True,
    )


# --- silent PCM watchdog ----------------------------------------------------


def test_silent_pcm_watchdog_recovers_internal_direct_capture_once_after_real_signal():
    watchdog = SilentPCMRecoveryWatchdog()

    assert not watchdog.should_recover(True, True, rms=0, peak=0), "no signal seen yet"
    assert not watchdog.should_recover(True, True, rms=0.02, peak=0.08), "real signal"
    for _ in range(REQUIRED_SILENT_WINDOWS - 1):
        assert not watchdog.should_recover(True, True, rms=0, peak=0)
    assert watchdog.should_recover(True, True, rms=0, peak=0)
    assert not watchdog.should_recover(True, True, rms=0, peak=0), "recovery is requested once"


def test_silent_pcm_watchdog_ignores_external_and_legacy_captures():
    external = SilentPCMRecoveryWatchdog()
    assert not external.should_recover(False, True, rms=0.02, peak=0.08)
    for _ in range(REQUIRED_SILENT_WINDOWS + 1):
        assert not external.should_recover(False, True, rms=0, peak=0)

    legacy = SilentPCMRecoveryWatchdog()
    assert not legacy.should_recover(True, False, rms=0.02, peak=0.08)
    for _ in range(REQUIRED_SILENT_WINDOWS + 1):
        assert not legacy.should_recover(True, False, rms=0, peak=0)


def test_silent_pcm_watchdog_resets_on_returning_signal():
    watchdog = SilentPCMRecoveryWatchdog()
    watchdog.should_recover(True, True, rms=0.02, peak=0.08)
    assert not watchdog.should_recover(True, True, rms=0, peak=0)
    assert not watchdog.should_recover(True, True, rms=0.02, peak=0.08), "signal returned"
    assert not watchdog.should_recover(True, True, rms=0, peak=0)
    assert not watchdog.should_recover(True, True, rms=0, peak=0)
    assert watchdog.should_recover(True, True, rms=0, peak=0)


def test_low_ambient_room_noise_is_not_treated_as_digital_silence():
    watchdog = SilentPCMRecoveryWatchdog()
    watchdog.should_recover(True, True, rms=0.02, peak=0.08)
    for _ in range(REQUIRED_SILENT_WINDOWS + 2):
        assert not watchdog.should_recover(True, True, rms=0.0001, peak=0.0004)


# --- topology change policy -------------------------------------------------


def test_prewarm_follows_the_direct_capture_setting():
    assert should_prewarm_capture(True)
    assert not should_prewarm_capture(False)


def test_engine_configuration_changes_recover_only_during_capture_transitions():
    assert should_recover_engine_configuration_change(is_running=True, is_starting=False)
    assert should_recover_engine_configuration_change(is_running=False, is_starting=True)
    assert not should_recover_engine_configuration_change(is_running=False, is_starting=False)


def test_resolved_priority_input_change_detection():
    priority = ["usb", "internal"]
    assert not did_resolved_priority_input_change(priority, {"usb", "internal"}, {"usb"})
    assert did_resolved_priority_input_change(priority, {"usb", "internal"}, {"internal"})
    assert did_resolved_priority_input_change(priority, {"internal"}, {"usb", "internal"})


def test_resolved_priority_identity_change_detects_a_replugged_device():
    priority = ["usb"]
    assert not did_resolved_priority_input_identity_change(priority, {"usb": 7}, {"usb": 7})
    # Same node name, new object id: the interface was replugged.
    assert did_resolved_priority_input_identity_change(priority, {"usb": 7}, {"usb": 9})
    assert did_resolved_priority_input_identity_change(priority, {"usb": 7}, {})
    assert not did_resolved_priority_input_identity_change(priority, {}, {})


def test_input_selection_reconciles_only_when_it_can_change_something():
    priority = ["usb", "internal"]

    # Everything disappeared and nothing was there before: nothing to do.
    assert not should_reconcile_input_selection(priority, False, set(), set())
    # Everything disappeared after having a choice: reconcile.
    assert should_reconcile_input_selection(priority, False, {"usb"}, set())
    # Same winning device: no reconciliation unless a migration is pending.
    assert not should_reconcile_input_selection(priority, False, {"usb"}, {"usb"})
    assert should_reconcile_input_selection(priority, True, {"usb"}, {"usb"})
    # With no priority list at all, any change is worth reconciling.
    assert should_reconcile_input_selection([], False, {"usb"}, {"usb"})
