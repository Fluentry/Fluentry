"""Host capability probes.

macOS asked `CPUArchitecture.isAppleSilicon` and the OS version. The Linux
equivalents that actually gate behaviour are total RAM, the session type
(X11 vs Wayland), and which input/clipboard helpers are installed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


@lru_cache(maxsize=1)
def total_memory_gb() -> float | None:
    """Total system RAM in GiB, or None when it cannot be determined."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return (pages * page_size) / (1024**3)


@lru_cache(maxsize=1)
def cpu_architecture() -> str:
    return platform.machine()


@lru_cache(maxsize=1)
def has_avx2() -> bool:
    """whisper.cpu throughput depends heavily on AVX2 availability."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            return " avx2 " in f" {handle.read()} "
    except OSError:
        return False


SESSION_WAYLAND = "wayland"
SESSION_X11 = "x11"
SESSION_HEADLESS = "headless"


def session_type() -> str:
    """Which display server the app is running under."""
    declared = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if declared == "wayland" and os.environ.get("WAYLAND_DISPLAY"):
        return SESSION_WAYLAND
    if os.environ.get("WAYLAND_DISPLAY"):
        return SESSION_WAYLAND
    if declared == "x11" or os.environ.get("DISPLAY"):
        return SESSION_X11
    return SESSION_HEADLESS


def has_x_server() -> bool:
    """True when an X11 server (including XWayland) is reachable."""
    return bool(os.environ.get("DISPLAY"))


@dataclass(frozen=True)
class ToolAvailability:
    """Which external helpers are installed.

    These replace the macOS Accessibility and Input Monitoring permissions:
    on Linux the equivalent "can we type into other apps" question is answered
    by whether a suitable synthetic-input tool exists for this session type.
    """

    xdotool: bool
    ydotool: bool
    wtype: bool
    wl_copy: bool
    xclip: bool
    xsel: bool
    notify_send: bool
    pactl: bool
    pw_cli: bool

    @property
    def can_synthesize_input(self) -> bool:
        return self.xdotool or self.ydotool or self.wtype

    @property
    def can_access_clipboard(self) -> bool:
        return self.wl_copy or self.xclip or self.xsel


def detect_tools() -> ToolAvailability:
    def present(name: str) -> bool:
        return shutil.which(name) is not None

    return ToolAvailability(
        xdotool=present("xdotool"),
        ydotool=present("ydotool"),
        wtype=present("wtype"),
        wl_copy=present("wl-copy"),
        xclip=present("xclip"),
        xsel=present("xsel"),
        notify_send=present("notify-send"),
        pactl=present("pactl"),
        pw_cli=present("pw-cli"),
    )


def uinput_is_writable() -> bool:
    """Whether `/dev/uinput` can be opened for synthetic input (ydotool path)."""
    return os.access("/dev/uinput", os.W_OK)


def input_devices_readable() -> bool:
    """Whether evdev keyboards can be read for a global hotkey grab."""
    try:
        entries = os.listdir("/dev/input")
    except OSError:
        return False
    return any(
        os.access(os.path.join("/dev/input", entry), os.R_OK)
        for entry in entries
        if entry.startswith("event")
    )


def desktop_environment() -> str:
    for variable in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION"):
        value = os.environ.get(variable)
        if value:
            return value.split(":")[0].strip().lower()
    return "unknown"


@lru_cache(maxsize=1)
def distribution_name() -> str:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def screen_is_locked() -> bool:
    """Ask logind whether the current session is locked."""
    try:
        result = subprocess.run(
            ["loginctl", "show-session", "self", "-p", "LockedHint"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    from ..services.hotkey_events import session_is_locked

    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            return session_is_locked({key.strip(): value.strip().lower() == "yes"})
    return False
