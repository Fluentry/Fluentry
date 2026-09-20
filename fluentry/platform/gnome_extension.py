"""Enabling the Fluentry Focus GNOME Shell extension.

The package ships a small Shell extension (`fluentry-focus@…`) that tells
Fluentry which window is focused, which is the only way, on GNOME Wayland,
to know a terminal is focused and send it the terminal paste shortcut
(Ctrl+Shift+V) instead of Ctrl+V. Debian installs the extension's *files*
system-wide, but enabling an extension is a per-user setting, and GNOME on
Wayland only *loads* a newly enabled extension at the next login.

So "install the package" is not enough on GNOME: the extension has to be
enabled for the user, and the session restarted once. This module does the
enabling itself - it adds the extension to the user's
`org.gnome.shell enabled-extensions`, exactly as the Extensions app would,
which survives to the next login - and reports which of a few honest states
the user is in, so the UI can say plainly when a one-time log out and back
in is still needed. It never claims to have done the part it cannot: the
re-login is a GNOME limitation no application can work around.

Everything here is a no-op off GNOME, where the compositor exposes the
focused window directly and no extension is involved.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from enum import Enum
from pathlib import Path

UUID = "fluentry-focus@fluentry.github.io"
SCHEMA = "org.gnome.shell"
KEY = "enabled-extensions"
BUS_NAME = "org.fluentry.Focus"

_COMMAND_TIMEOUT = 2.0


class TerminalSupport(Enum):
    """Where the user stands on GNOME terminal support."""

    #: Not GNOME (or not Wayland-hidden): the compositor exposes the focused
    #: window directly, so terminals already get the right paste shortcut.
    NOT_NEEDED = "not_needed"
    #: On GNOME but the extension's files are not installed.
    NOT_INSTALLED = "not_installed"
    #: Enabled and its service is answering: terminals work now.
    ACTIVE = "active"
    #: Enabled (by us or already), but GNOME has not loaded it yet - the user
    #: must log out and back in once.
    NEEDS_RELOGIN = "needs_relogin"


def _on_gnome() -> bool:
    return "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


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


def _extension_directories() -> list[Path]:
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    roots = [home, *data_dirs.split(":")]
    return [Path(root) / "gnome-shell" / "extensions" / UUID for root in roots if root]


def files_present() -> bool:
    """Whether the extension is installed on disk (by the package or the user)."""
    return any((directory / "metadata.json").is_file() for directory in _extension_directories())


def _enabled_list() -> list[str]:
    raw = _run(["gsettings", "get", SCHEMA, KEY])
    if not raw:
        return []
    raw = raw.strip()
    # Empty arrays print as "@as []"; a populated one as "['a', 'b']".
    if raw.startswith("@as"):
        raw = raw[len("@as"):].strip()
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def enabled_in_settings() -> bool:
    return UUID in _enabled_list()


def _enable_in_settings() -> bool:
    """Add the extension to the user's enabled list. Returns whether it is now set."""
    current = _enabled_list()
    if UUID in current:
        return True
    updated = [*current, UUID]
    # gsettings accepts a Python-style list literal for an 'as' value.
    written = _run(["gsettings", "set", SCHEMA, KEY, str(updated)])
    return written is not None and UUID in _enabled_list()


def service_live() -> bool:
    """Whether the extension's D-Bus service is actually answering right now."""
    output = _run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.NameHasOwner",
            BUS_NAME,
        ]
    )
    return bool(output) and "true" in output


def status() -> TerminalSupport:
    """Report terminal-support state without changing anything."""
    if not _on_gnome():
        return TerminalSupport.NOT_NEEDED
    if not files_present():
        return TerminalSupport.NOT_INSTALLED
    if service_live():
        return TerminalSupport.ACTIVE
    return TerminalSupport.NEEDS_RELOGIN


def ensure_enabled() -> TerminalSupport:
    """Enable the extension for this user if needed, and report the state.

    Safe to call on every startup: it only writes the setting when the
    extension is installed, applies, and is not already enabled. The write
    takes effect at the next login - which is why a `NEEDS_RELOGIN` result
    is honest about there still being one manual step left.
    """
    if not _on_gnome():
        return TerminalSupport.NOT_NEEDED
    if not files_present():
        return TerminalSupport.NOT_INSTALLED
    if service_live():
        return TerminalSupport.ACTIVE
    if not enabled_in_settings():
        _enable_in_settings()
    return TerminalSupport.NEEDS_RELOGIN
