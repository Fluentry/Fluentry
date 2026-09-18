"""Enumerate audio devices from the running sound server.

Replaces the CoreAudio HAL queries in `AudioDeviceService`. Three backends are
tried in order, so the app works on a plain PipeWire desktop, a PulseAudio
one, or bare ALSA:

1. **PipeWire** — `pw-dump` for nodes, `pw-metadata` for the default source.
2. **PulseAudio / pipewire-pulse** — `pactl -f json list sources`.
3. **ALSA** — `arecord -L`, which always exists when a sound card does.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable

from ..services.audio_device import AudioDeviceInfo, TransportType

_COMMAND_TIMEOUT = 5.0


def _run(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _active_input_port(node: dict) -> str | None:
    """The currently selected capture port, e.g. `analog-input-headset-mic`."""
    params = node.get("info", {}).get("params", {}) or {}
    for route in params.get("Route") or []:
        if isinstance(route, dict) and route.get("direction") == "Input":
            name = route.get("name")
            if isinstance(name, str):
                return name
    props = node.get("info", {}).get("props", {}) or {}
    port = props.get("device.profile.name") or props.get("api.alsa.pcm.card")
    return port if isinstance(port, str) else None


# --- PipeWire ---------------------------------------------------------------


def pipewire_devices() -> list[AudioDeviceInfo] | None:
    output = _run(["pw-dump"])
    if output is None:
        return None
    try:
        objects = json.loads(output)
    except ValueError:
        return None
    if not isinstance(objects, list):
        return None

    devices: list[AudioDeviceInfo] = []
    for node in objects:
        if not isinstance(node, dict) or node.get("type") != "PipeWire:Interface:Node":
            continue
        info = node.get("info") or {}
        props = info.get("props") or {}
        media_class = props.get("media.class") or ""
        if not media_class.startswith("Audio/"):
            continue
        is_source = "Source" in media_class
        is_sink = "Sink" in media_class
        if not is_source and not is_sink:
            continue
        name = props.get("node.name")
        if not isinstance(name, str):
            continue
        # A monitor stream mirrors an output; it is not a microphone.
        if name.endswith(".monitor") or props.get("device.class") == "monitor":
            continue
        devices.append(
            AudioDeviceInfo(
                id=int(node.get("id") or 0),
                uid=name,
                name=str(props.get("node.description") or props.get("node.nick") or name),
                has_input=is_source,
                has_output=is_sink,
                transport_type=TransportType.from_properties(
                    {str(key): str(value) for key, value in props.items() if value is not None}
                ),
                input_port_id=_active_input_port(node) if is_source else None,
                is_alive=info.get("state") != "error",
            )
        )
    return devices


def pipewire_default_source_name() -> str | None:
    output = _run(["pw-metadata", "-n", "default"])
    if output is None:
        return None
    match = re.search(r"key:'default\.audio\.source' value:'([^']*)'", output)
    if match is None:
        match = re.search(r"key:'default\.configured\.audio\.source' value:'([^']*)'", output)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    name = payload.get("name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) else None


# --- PulseAudio -------------------------------------------------------------


def pulseaudio_devices() -> list[AudioDeviceInfo] | None:
    output = _run(["pactl", "-f", "json", "list", "sources"])
    if output is None:
        return None
    try:
        sources = json.loads(output)
    except ValueError:
        return None
    if not isinstance(sources, list):
        return None

    devices: list[AudioDeviceInfo] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = source.get("name")
        if not isinstance(name, str) or name.endswith(".monitor"):
            continue
        properties = {
            str(key): str(value) for key, value in (source.get("properties") or {}).items()
        }
        active_port = source.get("active_port")
        devices.append(
            AudioDeviceInfo(
                id=int(source.get("index") or 0),
                uid=name,
                name=str(source.get("description") or name),
                has_input=True,
                transport_type=TransportType.from_properties(properties),
                input_port_id=active_port if isinstance(active_port, str) else None,
                is_alive=source.get("state") != "INVALID",
            )
        )
    return devices


def pulseaudio_default_source_name() -> str | None:
    output = _run(["pactl", "get-default-source"])
    if output is None:
        return None
    name = output.strip()
    return name or None


# --- ALSA -------------------------------------------------------------------


def alsa_devices() -> list[AudioDeviceInfo]:
    output = _run(["arecord", "-L"])
    if output is None:
        return []
    devices: list[AudioDeviceInfo] = []
    identifier = 0
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t")) or not line.strip():
            continue
        uid = line.strip()
        description = ""
        if index + 1 < len(lines) and lines[index + 1].startswith((" ", "\t")):
            description = lines[index + 1].strip()
        identifier += 1
        devices.append(
            AudioDeviceInfo(
                id=identifier,
                uid=uid,
                name=description or uid,
                has_input=True,
                transport_type=TransportType.from_properties({"node.name": uid}),
            )
        )
    return devices


# --- public API -------------------------------------------------------------


class LinuxAudioDeviceManager:
    """`AudioDeviceManaging` implementation backed by the live sound server."""

    def __init__(self, lid_state_path: str | None = None) -> None:
        self._lid_state_path = lid_state_path

    def list_all_devices(self) -> list[AudioDeviceInfo]:
        for backend in (pipewire_devices, pulseaudio_devices):
            devices = backend()
            if devices:
                return devices
        return alsa_devices()

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        return [device for device in self.list_all_devices() if device.has_input]

    def default_input_device(self) -> AudioDeviceInfo | None:
        name = pipewire_default_source_name() or pulseaudio_default_source_name()
        if name is None:
            inputs = self.list_input_devices()
            return inputs[0] if inputs else None
        return next((device for device in self.list_input_devices() if device.uid == name), None)

    def is_input_device_usable(self, device: AudioDeviceInfo) -> bool:
        return device.is_alive and device.has_input

    @property
    def is_lid_closed(self) -> bool:
        return lid_is_closed(self._lid_state_path)


def lid_is_closed(state_path: str | None = None) -> bool:
    """Read the ACPI lid switch — the Linux equivalent of clamshell mode."""
    import glob

    candidates = [state_path] if state_path else sorted(glob.glob("/proc/acpi/button/lid/*/state"))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                return "closed" in handle.read().lower()
        except OSError:
            continue
    return False
