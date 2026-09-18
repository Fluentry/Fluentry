"""Port of the microphone migration and resolution coverage in AudioHardwareRecoveryTests."""

import pytest

from fluentry.persistence.settings_store import Keys, SettingsStore
from fluentry.persistence.settings_types import (
    MicrophonePriorityEntry,
    MicrophoneSelectionMode,
)
from fluentry.services.audio_device import (
    EXTERNAL_MICROPHONE_PORT_IDS,
    AudioDeviceInfo,
    TransportType,
)
from fluentry.services.microphone_coordinator import (
    MicrophoneChangeNotice,
    MicrophonePreferenceCoordinator,
    StaticDeviceManager,
)

_next_id = iter(range(1, 10_000))


def device(
    uid: str,
    name: str,
    transport: TransportType = TransportType.USB,
    input_port_id: str | None = None,
) -> AudioDeviceInfo:
    return AudioDeviceInfo(
        id=next(_next_id),
        uid=uid,
        name=name,
        has_input=True,
        transport_type=transport,
        input_port_id=input_port_id,
    )


def built_in(uid: str = "internal", name: str = "Built-in Microphone") -> AudioDeviceInfo:
    return device(uid, name, TransportType.BUILT_IN, "analog-input-internal-mic")


def coordinator(settings, devices, **kwargs) -> MicrophonePreferenceCoordinator:
    return MicrophonePreferenceCoordinator(settings=settings, devices=devices, **kwargs)


# --- device classification --------------------------------------------------


def test_audio_device_classifies_bluetooth_transports():
    for properties in (
        {"device.api": "bluez5"},
        {"device.bus": "bluetooth"},
        {"node.name": "bluez_input.AA_BB_CC"},
    ):
        assert TransportType.from_properties(properties) is TransportType.BLUETOOTH
    assert device("bt", "Headset", TransportType.BLUETOOTH).is_bluetooth


def test_audio_device_classifies_built_in_transport():
    assert TransportType.from_properties({"device.bus": "pci"}) is TransportType.BUILT_IN
    assert TransportType.from_properties({"device.form_factor": "internal"}) is TransportType.BUILT_IN
    assert built_in().is_built_in


def test_audio_device_classifies_usb_and_virtual_nodes():
    assert TransportType.from_properties({"device.bus": "usb"}) is TransportType.USB
    assert TransportType.from_properties({"node.name": "null-sink"}) is TransportType.VIRTUAL
    assert TransportType.from_properties({}) is TransportType.UNKNOWN


def test_lid_closed_hides_the_internal_microphone_but_not_a_jacked_headset():
    internal = built_in()
    headset = device(
        "internal-jack", "Headset Mic", TransportType.BUILT_IN, "analog-input-headset-mic"
    )
    assert internal.is_unavailable_when_lid_closed
    assert not headset.is_unavailable_when_lid_closed
    assert "analog-input-headset-mic" in EXTERNAL_MICROPHONE_PORT_IDS


# --- migration --------------------------------------------------------------


def test_legacy_system_mode_seeds_priority_from_current_default(settings: SettingsStore):
    settings.defaults.set(Keys.microphone_selection_mode, MicrophoneSelectionMode.SYSTEM.value)
    settings.preferred_input_device_uid = "internal"
    settings.microphone_selection_migration_version = 0
    devices = StaticDeviceManager(
        inputs=[built_in(), device("airpods", "AirPods", TransportType.BLUETOOTH)],
        default_input=device("airpods", "AirPods", TransportType.BLUETOOTH),
    )
    subject = coordinator(settings, devices)

    subject.migrate_microphone_priority_if_needed()

    assert settings.preferred_input_device_uid == "airpods"
    assert settings.microphone_selection_mode is MicrophoneSelectionMode.MANUAL
    assert settings.microphone_selection_migration_version == 4

    settings.record_input_device_selection("internal")
    subject.migrate_microphone_priority_if_needed()
    assert settings.preferred_input_device_uid == "internal"


def test_legacy_stored_microphone_without_mode_key_keeps_user_selection(settings: SettingsStore):
    settings.defaults.remove(Keys.microphone_selection_mode)
    settings.preferred_input_device_uid = "studio-mic"
    settings.microphone_selection_migration_version = 0
    devices = StaticDeviceManager(
        inputs=[built_in(), device("studio-mic", "Studio Mic")],
        default_input=built_in(),
    )

    coordinator(settings, devices).migrate_microphone_priority_if_needed()

    assert settings.microphone_priority[0].uid == "studio-mic"
    assert settings.preferred_input_device_uid == "studio-mic"
    assert settings.microphone_selection_mode is MicrophoneSelectionMode.MANUAL
    assert settings.microphone_selection_migration_version == 4


def test_fresh_install_keeps_priority_usable_while_waiting_for_the_system_default(
    settings: SettingsStore,
):
    settings.defaults.remove(Keys.microphone_selection_mode)
    settings.preferred_input_device_uid = None
    settings.microphone_selection_migration_version = 0
    fallback = device("fallback", "Available Fallback")

    unsettled = StaticDeviceManager(inputs=[fallback], default_input=device("system-default", "Default"))
    temporary = coordinator(settings, unsettled).reconcile_microphone_selection(
        unsettled.list_input_devices(), "system-default"
    )

    assert temporary == fallback
    assert [entry.uid for entry in settings.microphone_priority] == [fallback.uid]
    assert settings.preferred_input_device_uid is None
    assert settings.microphone_selection_migration_version == 0

    system_default = device("system-default", "System Default")
    settled = StaticDeviceManager(inputs=[fallback, system_default], default_input=system_default)
    coordinator(settings, settled).migrate_microphone_priority_if_needed()

    assert settings.microphone_priority[0].uid == system_default.uid
    assert settings.preferred_input_device_uid == system_default.uid
    assert settings.microphone_selection_migration_version == 4


def test_fresh_install_prioritizes_the_system_default_while_temporarily_unusable(
    settings: SettingsStore,
):
    settings.defaults.set(Keys.microphone_selection_mode, MicrophoneSelectionMode.SYSTEM.value)
    settings.preferred_input_device_uid = None
    settings.microphone_selection_migration_version = 0
    system_default = device("system-default", "System Default")
    fallback = device("fallback", "Available Fallback")
    devices = StaticDeviceManager(
        inputs=[fallback, system_default],
        default_input=system_default,
        unusable_uids=[system_default.uid],
    )

    resolved = coordinator(settings, devices).reconcile_microphone_selection(
        devices.list_input_devices(), system_default.uid
    )

    assert [entry.uid for entry in settings.microphone_priority] == [system_default.uid, fallback.uid]
    assert settings.preferred_input_device_uid == system_default.uid
    assert resolved == fallback
    assert settings.microphone_selection_migration_version == 4


def test_microphone_migration_waits_for_a_usable_device_list(settings: SettingsStore):
    settings.defaults.set(Keys.microphone_selection_mode, MicrophoneSelectionMode.SYSTEM.value)
    settings.preferred_input_device_uid = "airpods"
    settings.microphone_selection_migration_version = 0

    coordinator(settings, StaticDeviceManager()).migrate_microphone_priority_if_needed()

    assert settings.preferred_input_device_uid == "airpods"
    assert settings.microphone_selection_migration_version == 0


def test_manual_microphone_migration_preserves_available_selection(settings: SettingsStore):
    settings.defaults.set(Keys.microphone_selection_mode, MicrophoneSelectionMode.MANUAL.value)
    settings.preferred_input_device_uid = "studio-mic"
    settings.microphone_selection_migration_version = 0
    display = device("display-mic", "Display Mic")
    devices = StaticDeviceManager(
        inputs=[display, device("studio-mic", "Studio Mic")], default_input=display
    )

    coordinator(settings, devices).migrate_microphone_priority_if_needed()

    assert settings.preferred_input_device_uid == "studio-mic"
    assert settings.microphone_selection_migration_version == 4


def test_microphone_migration_without_the_stored_device_falls_back_to_the_default(
    settings: SettingsStore,
):
    settings.defaults.set(Keys.microphone_selection_mode, MicrophoneSelectionMode.SYSTEM.value)
    settings.preferred_input_device_uid = "internal"
    settings.microphone_selection_migration_version = 0
    studio = device("studio-mic", "Studio Mic")
    devices = StaticDeviceManager(inputs=[device("display-mic", "Display Mic"), studio], default_input=studio)

    coordinator(settings, devices).migrate_microphone_priority_if_needed()

    assert settings.preferred_input_device_uid == "studio-mic"
    assert settings.microphone_selection_migration_version == 4


def test_version_one_migration_repairs_forced_built_in_selection(settings: SettingsStore):
    settings.preferred_input_device_uid = "internal"
    settings.microphone_selection_migration_version = 1
    usb = device("usb", "USB Mic")
    devices = StaticDeviceManager(inputs=[built_in(), usb], default_input=usb)

    reconciled = coordinator(settings, devices).reconcile_microphone_selection(
        devices.list_input_devices(), usb.uid
    )

    assert reconciled.uid == "usb"
    assert settings.preferred_input_device_uid == "usb"
    assert settings.microphone_selection_migration_version == 4


def test_version_one_migration_repairs_unavailable_built_in_for_a_docked_user(
    settings: SettingsStore,
):
    settings.preferred_input_device_uid = "internal"
    settings.microphone_selection_migration_version = 1
    webcam = device("webcam", "Webcam Microphone")
    devices = StaticDeviceManager(inputs=[webcam], default_input=webcam)

    reconciled = coordinator(settings, devices).reconcile_microphone_selection(
        devices.list_input_devices(), webcam.uid
    )

    assert reconciled == webcam
    assert settings.preferred_input_device_uid == "webcam"
    assert settings.microphone_selection_migration_version == 4


def test_version_one_migration_preserves_disconnected_external_selection(settings: SettingsStore):
    settings.preferred_input_device_uid = "disconnected-studio-mic"
    settings.microphone_selection_migration_version = 1
    fallback = built_in()
    devices = StaticDeviceManager(inputs=[fallback], default_input=fallback)

    reconciled = coordinator(settings, devices).reconcile_microphone_selection(
        devices.list_input_devices(), fallback.uid
    )

    assert reconciled == fallback
    assert settings.preferred_input_device_uid == "disconnected-studio-mic"
    assert [entry.uid for entry in settings.microphone_priority] == [
        "disconnected-studio-mic",
        fallback.uid,
    ]
    assert settings.microphone_selection_migration_version == 4


# --- resolution -------------------------------------------------------------


def test_coordinator_keeps_available_user_selection(settings: SettingsStore):
    settings.preferred_input_device_uid = "studio-mic"
    studio = device("studio-mic", "Studio Mic")
    devices = StaticDeviceManager(inputs=[built_in(), studio], default_input=built_in())

    assert coordinator(settings, devices).input_device_for_capture() == studio
    assert settings.preferred_input_device_uid == "studio-mic"


def test_coordinator_uses_default_temporarily_and_restores_selection(settings: SettingsStore):
    settings.preferred_input_device_uid = "airpods"
    settings.microphone_selection_migration_version = 2
    internal = built_in()
    usb = device("usb", "USB Mic")
    devices = StaticDeviceManager(inputs=[internal, usb], default_input=usb)
    subject = coordinator(settings, devices)

    assert subject.input_device_for_capture().uid == "usb"
    assert settings.preferred_input_device_uid == "airpods"

    settled = subject.reconcile_microphone_selection(devices.list_input_devices(), usb.uid)
    assert settled.uid == "usb"
    assert settings.preferred_input_device_uid == "airpods"

    airpods = device("airpods", "AirPods", TransportType.BLUETOOTH)
    after_reconnect = subject.reconcile_microphone_selection([internal, airpods], internal.uid)
    assert after_reconnect == airpods
    assert settings.preferred_input_device_uid == "airpods"


def test_coordinator_uses_current_input_when_the_selection_is_gone(settings: SettingsStore):
    settings.preferred_input_device_uid = "disconnected"
    settings.microphone_selection_migration_version = 2
    current = device("usb", "USB Mic")
    devices = StaticDeviceManager(inputs=[device("other", "Other Mic"), current], default_input=current)
    subject = coordinator(settings, devices)

    assert subject.input_device_for_capture() == current
    assert settings.preferred_input_device_uid == "disconnected"

    assert subject.reconcile_microphone_selection(devices.list_input_devices(), current.uid) == current
    assert settings.preferred_input_device_uid == "disconnected"


def test_microphone_priority_wins_over_default_and_built_in(settings: SettingsStore):
    internal = built_in()
    usb = device("usb", "USB Microphone")
    settings.microphone_priority = [
        MicrophonePriorityEntry(usb.uid, usb.name),
        MicrophonePriorityEntry(internal.uid, internal.name),
    ]
    settings.microphone_selection_mode = MicrophoneSelectionMode.MANUAL
    settings.microphone_selection_migration_version = 4
    devices = StaticDeviceManager(inputs=[internal, usb], default_input=internal)

    assert coordinator(settings, devices).input_device_for_capture() == usb


def test_priority_skips_an_unusable_device_and_restores_it_after_reconnect(settings: SettingsStore):
    usb = device("usb", "USB Microphone")
    internal = built_in()
    settings.microphone_priority = [
        MicrophonePriorityEntry(usb.uid, usb.name),
        MicrophonePriorityEntry(internal.uid, internal.name),
    ]
    settings.microphone_selection_migration_version = 4

    broken = StaticDeviceManager(inputs=[usb, internal], default_input=internal, unusable_uids=[usb.uid])
    assert coordinator(settings, broken).input_device_for_capture() == internal

    healthy = StaticDeviceManager(inputs=[usb, internal], default_input=internal)
    assert coordinator(settings, healthy).input_device_for_capture() == usb
    # The saved order is never rewritten by a temporary failure.
    assert [entry.uid for entry in settings.microphone_priority] == [usb.uid, internal.uid]


def test_lid_closed_skips_the_internal_microphone(settings: SettingsStore):
    internal = built_in()
    webcam = device("webcam", "Webcam Mic")
    settings.microphone_priority = [
        MicrophonePriorityEntry(internal.uid, internal.name),
        MicrophonePriorityEntry(webcam.uid, webcam.name),
    ]
    settings.microphone_selection_migration_version = 4

    closed = StaticDeviceManager(inputs=[internal, webcam], default_input=internal, lid_closed=True)
    assert coordinator(settings, closed).input_device_for_capture() == webcam

    open_lid = StaticDeviceManager(inputs=[internal, webcam], default_input=internal)
    assert coordinator(settings, open_lid).input_device_for_capture() == internal


def test_lid_closed_keeps_a_jacked_headset_on_the_built_in_device(settings: SettingsStore):
    headset = device(
        "internal-jack", "Headset Mic", TransportType.BUILT_IN, "analog-input-headset-mic"
    )
    settings.microphone_selection_migration_version = 4
    devices = StaticDeviceManager(inputs=[headset], default_input=headset, lid_closed=True)

    assert coordinator(settings, devices).input_device_for_capture() == headset


def test_suppressed_microphones_are_never_resolved(settings: SettingsStore):
    usb = device("usb", "USB Microphone")
    internal = built_in()
    settings.microphone_selection_migration_version = 4
    settings.suppressed_microphone_uids = {usb.uid}
    devices = StaticDeviceManager(inputs=[usb, internal], default_input=usb)

    assert coordinator(settings, devices).input_device_for_capture() == internal


def test_excluded_uids_are_skipped_during_a_retry(settings: SettingsStore):
    usb = device("usb", "USB Microphone")
    internal = built_in()
    settings.microphone_selection_migration_version = 4
    devices = StaticDeviceManager(inputs=[usb, internal], default_input=usb)
    subject = coordinator(settings, devices)

    assert subject.input_device_for_capture(excluding=[usb.uid]) == internal
    assert subject.input_device_for_capture(excluding=[usb.uid, internal.uid]) is None


# --- notices ----------------------------------------------------------------


def test_resolved_microphone_is_not_marked_active_until_first_pcm(settings: SettingsStore):
    usb = device("usb", "USB Microphone")
    settings.microphone_selection_migration_version = 4
    devices = StaticDeviceManager(inputs=[usb], default_input=usb)
    subject = coordinator(settings, devices)

    subject.reconcile_microphone_selection([usb], usb.uid)
    assert subject.confirmed_active_input_uid is None

    subject.confirm_active_selection(usb.uid, usb.name)
    assert subject.confirmed_active_input_uid == usb.uid


def test_a_selection_change_notifies_only_after_a_first_resolution(settings: SettingsStore):
    notices: list[MicrophoneChangeNotice] = []
    settings.microphone_selection_migration_version = 4
    subject = coordinator(settings, StaticDeviceManager(), present_notice=notices.append)

    subject.report_resolved_selection("a", "Mic A")
    assert notices == [], "the first resolution is not a change"

    subject.report_resolved_selection("b", "Mic B")
    assert len(notices) == 1
    assert notices[0].previous_name == "Mic A"
    assert notices[0].current_name == "Mic B"

    subject.report_resolved_selection("b", "Mic B")
    assert len(notices) == 1, "re-resolving the same device is not a change"


def test_notices_are_suppressed_during_onboarding_and_when_alerts_are_off(settings: SettingsStore):
    notices: list[MicrophoneChangeNotice] = []
    settings.microphone_selection_migration_version = 4

    onboarding = coordinator(
        settings,
        StaticDeviceManager(),
        present_notice=notices.append,
        should_show_onboarding=lambda: True,
    )
    onboarding.report_resolved_selection("a", "Mic A")
    onboarding.report_resolved_selection("b", "Mic B")
    assert notices == []

    settings.show_microphone_change_alerts = False
    quiet = coordinator(settings, StaticDeviceManager(), present_notice=notices.append)
    quiet.report_resolved_selection("a", "Mic A")
    quiet.report_resolved_selection("b", "Mic B")
    assert notices == []


def test_losing_the_active_microphone_notifies_once(settings: SettingsStore):
    notices: list[MicrophoneChangeNotice] = []
    settings.microphone_selection_migration_version = 4
    subject = coordinator(settings, StaticDeviceManager(), present_notice=notices.append)

    subject.mark_active_selection_unavailable()
    assert notices == [], "nothing was ever confirmed active"

    subject.confirm_active_selection("usb", "USB Microphone")
    subject.mark_active_selection_unavailable()
    assert len(notices) == 1
    assert notices[0].previous_name == "USB Microphone"
    assert notices[0].current_name is None

    subject.mark_active_selection_unavailable()
    assert len(notices) == 1


def test_confirmed_active_input_clears_when_the_device_disappears(settings: SettingsStore):
    usb = device("usb", "USB Microphone")
    internal = built_in()
    settings.microphone_selection_migration_version = 4
    subject = coordinator(settings, StaticDeviceManager(inputs=[usb, internal], default_input=usb))

    subject.confirm_active_selection(usb.uid, usb.name)
    subject.reconcile_microphone_selection([internal], internal.uid)

    assert subject.confirmed_active_input_uid is None
