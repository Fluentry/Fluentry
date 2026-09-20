"""Which application is focused right now.

Replaces `ActiveAppMonitor`. The focused app decides which prompt profile
applies and which app-specific formatting rules run, so the app needs a name
and a stable identifier for it.

X11 exposes this through `_NET_ACTIVE_WINDOW`, but Wayland deliberately
hides it from ordinary clients. Where a compositor
offers an interface (GNOME Shell's Eval is disabled by default; KDE exposes
KWin scripting) it is used, and otherwise the context is simply empty —
which every consumer already handles, because app-specific behaviour is an
enhancement rather than a requirement.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

COMMAND_TIMEOUT = 1.5


@dataclass(frozen=True)
class WindowContext:
    app_id: str | None = None
    app_name: str | None = None
    title: str | None = None

    @property
    def is_known(self) -> bool:
        return bool(self.app_id or self.app_name or self.title)


EMPTY_CONTEXT = WindowContext()


def _run(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def xdotool_context() -> WindowContext | None:
    """X11 and XWayland, via `_NET_ACTIVE_WINDOW`."""
    window_id = _run(["xdotool", "getactivewindow"])
    if not window_id:
        return None
    window_id = window_id.strip()
    title = _run(["xdotool", "getwindowname", window_id])
    classname = _run(["xprop", "-id", window_id, "WM_CLASS"])

    app_id = None
    app_name = None
    if classname:
        # WM_CLASS(STRING) = "instance", "Class"
        values = re.findall(r'"([^"]*)"', classname)
        if values:
            app_id = values[0].lower()
            app_name = values[-1]
    context = WindowContext(
        app_id=app_id,
        app_name=app_name or (app_id.title() if app_id else None),
        title=title.strip() if title else None,
    )
    # Under XWayland a native Wayland window still has an X id but no name or
    # class, which is indistinguishable from "not detected".
    return context if context.is_known else None


def kwin_context() -> WindowContext | None:
    """KDE Plasma, via KWin's D-Bus interface."""
    output = _run(
        [
            "qdbus",
            "org.kde.KWin",
            "/KWin",
            "org.kde.KWin.activeWindowInfo",
        ]
    )
    if not output:
        return None
    try:
        payload = json.loads(output)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    app_id = payload.get("resourceClass") or payload.get("desktopFile")
    return WindowContext(
        app_id=str(app_id).lower() if app_id else None,
        app_name=payload.get("resourceName") or (str(app_id) if app_id else None),
        title=payload.get("caption"),
    )


def hyprland_context() -> WindowContext | None:
    """Hyprland, via `hyprctl`."""
    output = _run(["hyprctl", "-j", "activewindow"])
    if not output:
        return None
    try:
        payload = json.loads(output)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not payload:
        return None
    app_id = payload.get("class")
    return WindowContext(
        app_id=str(app_id).lower() if app_id else None,
        app_name=payload.get("initialClass") or app_id,
        title=payload.get("title"),
    )


def sway_context() -> WindowContext | None:
    """Sway and other i3-compatible compositors, via their IPC."""
    output = _run(["swaymsg", "-t", "get_tree"])
    if not output:
        return None
    try:
        tree = json.loads(output)
    except ValueError:
        return None

    def find_focused(node):
        if isinstance(node, dict):
            if node.get("focused"):
                return node
            for key in ("nodes", "floating_nodes"):
                for child in node.get(key) or []:
                    found = find_focused(child)
                    if found is not None:
                        return found
        return None

    focused = find_focused(tree)
    if focused is None:
        return None
    app_id = focused.get("app_id") or (focused.get("window_properties") or {}).get("class")
    return WindowContext(
        app_id=str(app_id).lower() if app_id else None,
        app_name=str(app_id) if app_id else None,
        title=focused.get("name"),
    )


#: Remembers that the Fluentry Focus service is absent, so a lookup does
#: not spawn a gdbus process on every single dictation when the extension
#: is not installed. Short-lived, because the extension can be enabled
#: within a session (a re-login makes a new process and clears this anyway).
_GNOME_FOCUS_ABSENT_UNTIL = 0.0
_GNOME_FOCUS_ABSENCE_SECONDS = 30.0


def _on_gnome() -> bool:
    current = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return "GNOME" in current.upper()


def gnome_context() -> WindowContext | None:
    """GNOME Shell, via the Fluentry Focus extension.

    GNOME Wayland gives ordinary clients no way to read the focused window
    (Introspect.GetWindows is allowed only to portals, Eval is disabled),
    so the app ships a small Shell extension that publishes exactly the
    focused window's class and title on the session bus. Absent the
    extension this simply returns None, like any other provider that does
    not apply - and remembers that briefly, so it is not paying for a
    gdbus call on every dictation.
    """
    global _GNOME_FOCUS_ABSENT_UNTIL

    if not _on_gnome():
        return None
    import time

    if time.monotonic() < _GNOME_FOCUS_ABSENT_UNTIL:
        return None
    output = _run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.fluentry.Focus",
            "--object-path",
            "/org/fluentry/Focus",
            "--method",
            "org.fluentry.Focus.GetFocused",
        ]
    )
    if not output:
        # No service answered: stop asking for a while.
        _GNOME_FOCUS_ABSENT_UNTIL = time.monotonic() + _GNOME_FOCUS_ABSENCE_SECONDS
        return None
    # gdbus prints a tuple literal: ('gnome-terminal-server', 'user@host: ~')
    values = re.findall(r"'((?:[^'\\]|\\.)*)'", output.strip())
    if len(values) < 2:
        return None
    app_id = values[0].encode().decode("unicode_escape") or None
    title = values[1].encode().decode("unicode_escape") or None
    context = WindowContext(
        app_id=app_id.lower() if app_id else None,
        app_name=app_id or None,
        title=title,
    )
    return context if context.is_known else None


def active_window_context() -> WindowContext:
    """Best available answer, or an empty context when the desktop hides it."""
    for provider in (
        hyprland_context,
        sway_context,
        kwin_context,
        gnome_context,
        xdotool_context,
    ):
        try:
            context = provider()
        except Exception:
            continue
        if context is not None and context.is_known:
            return context
    return EMPTY_CONTEXT


@lru_cache(maxsize=1)
def active_window_support() -> str:
    """A short description for the Settings screen."""
    from .system_capabilities import SESSION_WAYLAND, session_type

    for name, provider in (
        ("Hyprland", hyprland_context),
        ("Sway", sway_context),
        ("KWin", kwin_context),
        ("the Fluentry Focus extension", gnome_context),
        ("X11", xdotool_context),
    ):
        try:
            context = provider()
        except Exception:
            continue
        if context is not None and context.is_known:
            return f"Detected through {name}."
    if session_type() == SESSION_WAYLAND:
        return (
            "This compositor does not expose the focused window. On GNOME, enable "
            "the Fluentry Focus extension (then log out and back in) so terminals "
            "and per-app formatting are recognised."
        )
    return "The focused window could not be determined."
