"""Resolve the key that pastes on the user's keyboard layout.

`PasteKeyCodeResolver` and `PasteKeyCodeCache`, retargeted at xkb.

The macOS problem was Dvorak and non-Latin layouts, where the physical key
that types "v" is not the key that means Paste. Linux has the same problem
whenever a non-QWERTY layout is active, so the fix is identical: ask the
current layout which keycode produces "v", cache the answer, and refresh it
when the layout changes — never on the paste path itself, which must not block
on a keymap query.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from typing import Callable

from ..models.keycodes import KEY_V

#: X11 keycodes are evdev codes plus 8.
X11_KEYCODE_OFFSET = 8

#: Used whenever layout data cannot be read.
FALLBACK_PASTE_KEY_CODE = KEY_V

_KEYCODE_LINE = re.compile(r"^keycode\s+(\d+)\s*=\s*(.*)$")


def resolve_from_keymap(keymap: str | None) -> int:
    """Find the evdev keycode whose unmodified keysym is `v`."""
    if not keymap:
        return FALLBACK_PASTE_KEY_CODE
    for line in keymap.splitlines():
        match = _KEYCODE_LINE.match(line.strip())
        if match is None:
            continue
        keysyms = match.group(2).split()
        if not keysyms:
            continue
        if keysyms[0] == "v":
            x11_keycode = int(match.group(1))
            evdev_keycode = x11_keycode - X11_KEYCODE_OFFSET
            return evdev_keycode if evdev_keycode > 0 else FALLBACK_PASTE_KEY_CODE
    return FALLBACK_PASTE_KEY_CODE


def read_current_keymap() -> str | None:
    if shutil.which("xmodmap") is None:
        return None
    try:
        result = subprocess.run(
            ["xmodmap", "-pke"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def current_paste_key_code() -> int:
    return resolve_from_keymap(read_current_keymap())


class PasteKeyCodeCache:
    """One process-wide snapshot, refreshed on layout-change notifications.

    Paste requests only read the snapshot; they never query the layout, so a
    slow or blocked keymap query can never delay text insertion.
    """

    def __init__(self, resolve: Callable[[], int] | None = None) -> None:
        self._resolve = resolve or current_paste_key_code
        self._lock = threading.Lock()
        self._key_code = FALLBACK_PASTE_KEY_CODE
        self._started = False
        self.lookup_count = 0

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self.refresh()

    def refresh(self) -> None:
        try:
            updated = self._resolve()
        except Exception:
            updated = FALLBACK_PASTE_KEY_CODE
        with self._lock:
            self.lookup_count += 1
            self._key_code = updated

    def layout_did_change(self) -> None:
        """Call when the desktop reports a keyboard-layout change."""
        if not self._started:
            return
        self.refresh()

    def snapshot(self) -> int:
        with self._lock:
            return self._key_code
