"""Audio device model.

A device is described by an id, a stable UID and a
`transportType`. PipeWire (and PulseAudio) expose the same facts under
different names, so the model is preserved and only its sources change:

==========================  =============================================
Field                       Source (PipeWire / PulseAudio)
==========================  =============================================
`AudioObjectID`             node id (`object.id`)
device UID                  node name (`node.name`), stable across reboots
`transportType`             `device.bus` + `device.api`
input data source `'emic'`  active input port id, e.g.
                            `analog-input-headset-mic`
`isAlive`                   node still present and not in an error state
==========================  =============================================

The "unavailable when clamshell closed" idea carries over exactly: a laptop's
built-in microphone disappears when the lid is shut on a docked machine, while
an analog headset plugged into the same built-in audio device does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TransportType(str, Enum):
    UNKNOWN = "unknown"
    BUILT_IN = "builtin"
    BLUETOOTH = "bluetooth"
    USB = "usb"
    HDMI = "hdmi"
    VIRTUAL = "virtual"
    NETWORK = "network"

    @staticmethod
    def from_properties(properties: dict[str, str]) -> "TransportType":
        """Classify from PipeWire/PulseAudio node properties."""
        api = (properties.get("device.api") or "").lower()
        bus = (properties.get("device.bus") or "").lower()
        form_factor = (properties.get("device.form_factor") or "").lower()
        node_name = (properties.get("node.name") or "").lower()

        if api == "bluez5" or bus == "bluetooth" or "bluez" in node_name:
            return TransportType.BLUETOOTH
        if bus == "usb":
            return TransportType.USB
        if "hdmi" in node_name or form_factor == "hdmi":
            return TransportType.HDMI
        if api in ("null", "virtual") or node_name.startswith(("null", "virtual", "echo-cancel")):
            return TransportType.VIRTUAL
        if api in ("netjack2", "roc", "pulse-network") or bus == "network":
            return TransportType.NETWORK
        if bus in ("pci", "isa", "platform", "acp") or form_factor in ("internal", "speaker"):
            return TransportType.BUILT_IN
        return TransportType.UNKNOWN


#: Port ids PipeWire reports for a microphone plugged into the analog jack.
#: The Linux counterpart of Core Audio's `'emic'` external-microphone source.
EXTERNAL_MICROPHONE_PORT_IDS = frozenset(
    {
        "analog-input-headset-mic",
        "analog-input-headphone-mic",
        "analog-input-microphone",
        "analog-input-microphone-front",
        "analog-input-microphone-rear",
        "analog-input-linein",
    }
)


@dataclass(frozen=True)
class AudioDeviceInfo:
    id: int
    uid: str
    name: str
    has_input: bool = False
    has_output: bool = False
    transport_type: TransportType = TransportType.UNKNOWN
    input_port_id: str | None = None
    is_alive: bool = True

    @property
    def is_bluetooth(self) -> bool:
        return self.transport_type is TransportType.BLUETOOTH

    @property
    def is_built_in(self) -> bool:
        return self.transport_type is TransportType.BUILT_IN

    @property
    def is_unavailable_when_lid_closed(self) -> bool:
        """A built-in mic vanishes with the lid; a jacked-in headset does not."""
        return self.is_built_in and self.input_port_id not in EXTERNAL_MICROPHONE_PORT_IDS

    @property
    def is_usable_input(self) -> bool:
        return self.has_input and self.is_alive
