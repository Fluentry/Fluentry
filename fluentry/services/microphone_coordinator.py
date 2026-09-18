"""Resolve which microphone a capture should use.

A port of `MicrophonePreferenceCoordinator`. Two jobs:

1. **Migration** — older builds stored a single "preferred input" plus a
   system/manual mode. Version 4 stores an ordered priority list instead. The
   migration must not silently change which microphone a user was already
   getting, including when that device is currently disconnected.
2. **Resolution** — pick the highest-priority microphone that is actually
   usable right now, skipping suppressed devices and, on a laptop with the lid
   shut, the internal mic that is no longer reachable.

macOS called the lid-shut state "clamshell"; the Linux equivalent reads
`/proc/acpi/button/lid/*/state`, but the rule is identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Protocol, Sequence

from ..persistence.settings_store import MICROPHONE_PRIORITY_MIGRATION_VERSION, SettingsStore
from ..persistence.settings_types import MicrophoneSelectionMode
from .audio_device import AudioDeviceInfo


class AudioDeviceManaging(Protocol):
    @property
    def is_lid_closed(self) -> bool: ...

    def list_input_devices(self) -> list[AudioDeviceInfo]: ...

    def default_input_device(self) -> AudioDeviceInfo | None: ...

    def is_input_device_usable(self, device: AudioDeviceInfo) -> bool: ...


class StaticDeviceManager:
    """A fixed device snapshot; the default when no live backend is attached."""

    def __init__(
        self,
        inputs: Sequence[AudioDeviceInfo] = (),
        default_input: AudioDeviceInfo | None = None,
        lid_closed: bool = False,
        unusable_uids: Iterable[str] = (),
    ) -> None:
        self._inputs = list(inputs)
        self._default_input = default_input
        self._lid_closed = lid_closed
        self._unusable_uids = set(unusable_uids)

    @property
    def is_lid_closed(self) -> bool:
        return self._lid_closed

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        return list(self._inputs)

    def default_input_device(self) -> AudioDeviceInfo | None:
        return self._default_input

    def is_input_device_usable(self, device: AudioDeviceInfo) -> bool:
        return device.uid not in self._unusable_uids


class MicrophoneChangePresentation:
    SELECTION_CHANGE = "selectionChange"
    ACTIVE_CHANGE = "activeChange"
    STARTUP_SELECTION = "startupSelection"


@dataclass(frozen=True)
class MicrophoneChangeNotice:
    previous_name: str | None
    current_name: str | None
    presentation: str


def is_likely_built_in_microphone_uid(uid: str | None) -> bool:
    if not uid:
        return False
    normalized = uid.lower()
    return "builtin" in normalized or "built-in" in normalized or "internal" in normalized


class MicrophonePreferenceCoordinator:
    def __init__(
        self,
        settings: SettingsStore,
        devices: AudioDeviceManaging | None = None,
        present_notice: Callable[[MicrophoneChangeNotice], None] | None = None,
        should_show_onboarding: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.devices = devices or StaticDeviceManager()
        self._present_notice = present_notice
        self._should_show_onboarding = should_show_onboarding or (lambda: False)

        self.last_resolved_input_uid: str | None = None
        self.last_resolved_input_name: str | None = None
        self.confirmed_active_input_uid: str | None = None
        self._has_confirmed_active_selection = False
        self._last_confirmed_input_uid: str | None = None
        self._last_confirmed_input_name: str | None = None

    # --- migration --------------------------------------------------------

    @property
    def needs_microphone_priority_migration(self) -> bool:
        return (
            self.settings.microphone_selection_migration_version
            < MICROPHONE_PRIORITY_MIGRATION_VERSION
        )

    def migrate_microphone_priority_if_needed(
        self,
        available_inputs: Sequence[AudioDeviceInfo] | None = None,
        default_input_uid: str | None = None,
    ) -> None:
        if available_inputs is None:
            available_inputs = self.devices.list_input_devices()
            default_device = self.devices.default_input_device()
            default_input_uid = default_device.uid if default_device else None

        if not self.needs_microphone_priority_migration:
            self.settings.reconcile_microphone_priority(list(available_inputs))
            return

        migration_version = self.settings.microphone_selection_migration_version
        lid_closed = self.devices.is_lid_closed
        usable_inputs = [
            device
            for device in available_inputs
            if self._is_input_device_available(device, lid_closed)
        ]

        preferred_uid = self.settings.preferred_input_device_uid
        preferred_input = next((d for d in usable_inputs if d.uid == preferred_uid), None)
        enumerated_preferred_input = next(
            (d for d in available_inputs if d.uid == preferred_uid), None
        )
        enumerated_default_input = (
            next((d for d in available_inputs if d.uid == default_input_uid), None)
            if default_input_uid
            else None
        )
        default_input = (
            next((d for d in usable_inputs if d.uid == default_input_uid), None)
            if default_input_uid
            else None
        )
        # Put the system default first so the migrated list mirrors what the
        # user was actually hearing themselves through.
        migration_inputs = (
            [enumerated_default_input]
            + [d for d in available_inputs if d.uid != enumerated_default_input.uid]
            if enumerated_default_input is not None
            else list(available_inputs)
        )

        previous_mode = self.settings.stored_mic_selection_mode_for_migration
        has_stored_mode = self.settings.has_stored_mic_selection_mode_for_migration
        preserve_legacy_stored_selection = (
            migration_version == 0
            and not has_stored_mode
            and bool(preferred_uid)
            and preferred_uid not in self.settings.suppressed_microphone_uids
        )

        if migration_version >= 2 and preferred_uid:
            preferred_name = preferred_input.name if preferred_input else "Previously selected microphone"
            self._complete_migration(preferred_uid, preferred_name, migration_inputs)
            return

        preserve_disconnected_manual_selection = (
            migration_version == 0 and previous_mode is MicrophoneSelectionMode.MANUAL
        )
        preserve_disconnected_version_one_external = (
            migration_version == 1
            and not (enumerated_preferred_input.is_built_in if enumerated_preferred_input else False)
            and not is_likely_built_in_microphone_uid(preferred_uid)
        )

        if (
            preferred_input is None
            and (
                preserve_disconnected_manual_selection
                or preserve_disconnected_version_one_external
                or preserve_legacy_stored_selection
            )
            and preferred_uid
        ):
            self._complete_migration(
                preferred_uid, "Previously selected microphone", migration_inputs
            )
            return

        selected_input: AudioDeviceInfo | None
        if migration_version == 0 and previous_mode is MicrophoneSelectionMode.MANUAL:
            selected_input = (
                preferred_input
                or default_input
                or self._fallback_input(usable_inputs, default_input_uid)
            )
        elif preserve_legacy_stored_selection:
            selected_input = (
                preferred_input
                or default_input
                or self._fallback_input(usable_inputs, default_input_uid)
            )
        elif migration_version == 0:
            # A fresh install mirrors the system's selected input rather than
            # permanently promoting whichever device happened to enumerate
            # first. If the server has not exposed a default yet, migration
            # stays pending and capture uses a temporary fallback.
            selected_input = enumerated_default_input
        elif (
            migration_version == 1
            and preferred_input is not None
            and preferred_input.is_built_in
            and (default_input.uid if default_input else None) != preferred_input.uid
        ):
            # Repair only the known v1 "built-in first" mistake. Runtime
            # resolution itself never ranks devices by transport type.
            selected_input = default_input or self._fallback_input(usable_inputs, default_input_uid)
        else:
            selected_input = (
                preferred_input
                or default_input
                or self._fallback_input(usable_inputs, default_input_uid)
            )

        if selected_input is None:
            if migration_version == 0 and previous_mode is MicrophoneSelectionMode.SYSTEM:
                # Keep the visible priority list useful while the server has
                # not exposed its default input. Migration stays pending so a
                # later default-input event can still promote the real choice.
                preferred_before = self.settings.preferred_input_device_uid
                self.settings.reconcile_microphone_priority(list(available_inputs))
                self.settings.preferred_input_device_uid = preferred_before
                return
            self.settings.reconcile_microphone_priority(migration_inputs)
            return

        self._complete_migration(selected_input.uid, selected_input.name, migration_inputs)

    def _complete_migration(
        self, uid: str, name: str, migration_inputs: Sequence[AudioDeviceInfo]
    ) -> None:
        self.settings.record_input_device_selection(uid, name=name)
        self.settings.reconcile_microphone_priority(list(migration_inputs))
        self.settings.microphone_selection_mode = MicrophoneSelectionMode.MANUAL
        self.settings.microphone_selection_migration_version = MICROPHONE_PRIORITY_MIGRATION_VERSION

    # --- resolution -------------------------------------------------------

    def reconcile_microphone_selection(
        self, available_inputs: Sequence[AudioDeviceInfo], default_input_uid: str | None = None
    ) -> AudioDeviceInfo | None:
        self.migrate_microphone_priority_if_needed(available_inputs, default_input_uid)
        selected_input = self.input_device_for_capture(available_inputs, default_input_uid)
        self.report_resolved_selection(
            selected_input.uid if selected_input else None,
            selected_input.name if selected_input else None,
        )
        if self.confirmed_active_input_uid is not None and not any(
            device.uid == self.confirmed_active_input_uid and self.is_input_device_available(device)
            for device in available_inputs
        ):
            self.confirmed_active_input_uid = None
        return selected_input

    def input_device_for_capture(
        self,
        available_inputs: Sequence[AudioDeviceInfo] | None = None,
        default_input_uid: str | None = None,
        excluding: Iterable[str] = (),
    ) -> AudioDeviceInfo | None:
        if available_inputs is None:
            available_inputs = self.devices.list_input_devices()
            default_device = self.devices.default_input_device()
            default_input_uid = default_device.uid if default_device else None

        excluded = set(excluding)
        lid_closed = self.devices.is_lid_closed
        usable_inputs = [
            device
            for device in available_inputs
            if device.uid not in excluded and self._is_input_device_available(device, lid_closed)
        ]
        if not usable_inputs:
            return None

        for entry in self.settings.microphone_priority:
            match = next((device for device in usable_inputs if device.uid == entry.uid), None)
            if match is not None:
                return match

        preferred_uid = self.settings.preferred_input_device_uid
        if preferred_uid:
            preferred = next((d for d in usable_inputs if d.uid == preferred_uid), None)
            if preferred is not None:
                return preferred

        return self._fallback_input(usable_inputs, default_input_uid)

    def is_input_device_available(self, device: AudioDeviceInfo) -> bool:
        return self._is_input_device_available(device, self.devices.is_lid_closed)

    def _is_input_device_available(self, device: AudioDeviceInfo, lid_closed: bool) -> bool:
        if device.uid in self.settings.suppressed_microphone_uids:
            return False
        if lid_closed and device.is_unavailable_when_lid_closed:
            return False
        return self.devices.is_input_device_usable(device)

    @staticmethod
    def _fallback_input(
        inputs: Sequence[AudioDeviceInfo], default_input_uid: str | None
    ) -> AudioDeviceInfo | None:
        if not inputs:
            return None
        if default_input_uid:
            default_input = next((d for d in inputs if d.uid == default_input_uid), None)
            if default_input is not None:
                return default_input
        return inputs[0]

    # --- notices ----------------------------------------------------------

    def report_resolved_selection(self, uid: str | None, name: str | None) -> None:
        previous_uid = self.last_resolved_input_uid
        previous_name = self.last_resolved_input_name
        self.last_resolved_input_uid = uid
        self.last_resolved_input_name = name

        # Onboarding owns microphone feedback inside its own panel; a floating
        # notice there would cover the flow and duplicate the picker.
        if self._should_show_onboarding():
            return
        if previous_uid is None or uid == previous_uid:
            return

        self._show(
            MicrophoneChangeNotice(
                previous_name=previous_name,
                current_name=name,
                presentation=MicrophoneChangePresentation.SELECTION_CHANGE,
            )
        )

    def confirm_active_selection(self, uid: str | None, name: str | None) -> None:
        """Called on the first real PCM frame: the device is genuinely live."""
        previous_name = self._last_confirmed_input_name
        self._last_confirmed_input_uid = uid
        self._last_confirmed_input_name = name or previous_name
        self.confirmed_active_input_uid = uid
        self._has_confirmed_active_selection = True

    def mark_active_selection_unavailable(self) -> None:
        if not self._has_confirmed_active_selection or self._last_confirmed_input_uid is None:
            return
        previous_name = self._last_confirmed_input_name
        self.confirmed_active_input_uid = None
        self._last_confirmed_input_uid = None
        self._last_confirmed_input_name = None
        self._show(
            MicrophoneChangeNotice(
                previous_name=previous_name,
                current_name=None,
                presentation=MicrophoneChangePresentation.ACTIVE_CHANGE,
            )
        )

    def _show(self, notice: MicrophoneChangeNotice) -> None:
        if self._present_notice is None:
            return
        if not self.settings.show_microphone_change_alerts:
            return
        try:
            self._present_notice(notice)
        except Exception:
            pass
