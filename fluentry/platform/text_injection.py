"""Insert dictated text into whatever application has focus.

`TypingService`, retargeted at Linux input synthesis.

macOS had one mechanism (`CGEvent` plus the Accessibility API) gated behind
one permission. Linux has three, each with different reach:

===========  ==================  ===================================
Backend      Works with          Notes
===========  ==================  ===================================
`xdotool`    X11 and XWayland    no extra permission; the common case
`ydotool`    everything          needs access to `/dev/uinput`
`wtype`      Wayland             compositor must support the virtual
                                 keyboard protocol
===========  ==================  ===================================

Two insertion modes are preserved from the original: **standard**, which types
the text directly and leaves the clipboard untouched, and **reliable paste**,
which borrows the clipboard, sends the paste chord, and restores what was
there before.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, Sequence

from ..models.keycodes import ModifierFlags
from ..persistence.settings_types import TextInsertionMode
from .clipboard import TEXT_MIME, Clipboard, InMemoryClipboard, PreservedClipboardSnapshot

#: Marker MIME types that ask a clipboard manager to skip an entry.
#: `x-kde-passwordManagerHint` is deliberately NOT used: it means "secret",
#: which would be misleading for a dictation transcript (and hides it from the
#: user's own history for the wrong reason).
TRANSIENT_MIME_TYPES = (
    "application/x-copyq-hidden",
    "application/x-nospam",
)


def make_transient_clipboard_item(text: str) -> dict[str, bytes]:
    """Clipboard payload for the temporary write that drives a paste."""
    item: dict[str, bytes] = {TEXT_MIME: text.encode("utf-8"), "text/plain": text.encode("utf-8")}
    for marker in TRANSIENT_MIME_TYPES:
        item[marker] = b""
    return item


class InjectionBackend(Protocol):
    name: str

    def type_text(self, text: str) -> bool: ...

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool: ...

    @staticmethod
    def is_available() -> bool: ...


def _run(command: list[str], input_text: str | None = None, timeout: float = 10.0) -> bool:
    try:
        result = subprocess.run(
            command,
            input=input_text.encode("utf-8") if input_text is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class XdotoolBackend:
    name = "xdotool"

    #: xdotool's names for the modifiers a chord can carry.
    MODIFIER_NAMES = {
        ModifierFlags.CONTROL: "ctrl",
        ModifierFlags.SHIFT: "shift",
        ModifierFlags.ALT: "alt",
        ModifierFlags.SUPER: "super",
    }

    @staticmethod
    def is_available() -> bool:
        return shutil.which("xdotool") is not None

    def type_text(self, text: str) -> bool:
        if not text:
            return True
        # `--clearmodifiers` stops a still-held dictation hotkey from turning
        # the typed characters into shortcuts.
        return _run(["xdotool", "type", "--clearmodifiers", "--delay", "1", "--file", "-"], text)

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        chord = "+".join([*modifiers, key])
        return _run(["xdotool", "key", "--clearmodifiers", chord])


class YdotoolBackend:
    name = "ydotool"

    MODIFIER_NAMES = {
        ModifierFlags.CONTROL: "ctrl",
        ModifierFlags.SHIFT: "shift",
        ModifierFlags.ALT: "alt",
        ModifierFlags.SUPER: "super",
    }

    @staticmethod
    def is_available() -> bool:
        return shutil.which("ydotool") is not None

    def type_text(self, text: str) -> bool:
        if not text:
            return True
        return _run(["ydotool", "type", "--key-delay", "1", "--", text])

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        chord = "+".join([*modifiers, key])
        return _run(["ydotool", "key", chord])


class WtypeBackend:
    name = "wtype"

    MODIFIER_NAMES = {
        ModifierFlags.CONTROL: "ctrl",
        ModifierFlags.SHIFT: "shift",
        ModifierFlags.ALT: "alt",
        ModifierFlags.SUPER: "logo",
    }

    @staticmethod
    def is_available() -> bool:
        return shutil.which("wtype") is not None

    def type_text(self, text: str) -> bool:
        if not text:
            return True
        return _run(["wtype", "--", text])

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        command = ["wtype"]
        for modifier in modifiers:
            command += ["-M", modifier]
        command += ["-k", key]
        for modifier in reversed(list(modifiers)):
            command += ["-m", modifier]
        return _run(command)


class RecordingBackend:
    """Captures what would have been typed. Used by tests and dry runs."""

    name = "recording"

    def __init__(self, succeeds: bool = True) -> None:
        self.typed: list[str] = []
        self.chords: list[tuple[str, tuple[str, ...]]] = []
        self.succeeds = succeeds

    @staticmethod
    def is_available() -> bool:
        return True

    MODIFIER_NAMES = XdotoolBackend.MODIFIER_NAMES

    def type_text(self, text: str) -> bool:
        self.typed.append(text)
        return self.succeeds

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        self.chords.append((key, tuple(modifiers)))
        return self.succeeds


def available_backends() -> list[str]:
    return [
        backend.name
        for backend in (XdotoolBackend(), YdotoolBackend(), WtypeBackend())
        if type(backend).is_available()
    ]


def make_injection_backend(session_type: str | None = None):
    """Pick the backend that can reach the most applications on this session.

    On Wayland, `xdotool` only reaches XWayland clients, so a native-Wayland
    backend is preferred when one is installed.
    """
    from .system_capabilities import SESSION_WAYLAND, session_type as detect_session

    session = session_type or detect_session()
    if session == SESSION_WAYLAND:
        order = (YdotoolBackend, WtypeBackend, XdotoolBackend)
    else:
        order = (XdotoolBackend, YdotoolBackend, WtypeBackend)
    for backend_type in order:
        if backend_type.is_available():
            return backend_type()
    return RecordingBackend()


class InsertionResult(Enum):
    NOT_INSERTED = "notInserted"
    INSERTED = "inserted"
    ACTION_DISPATCHED = "actionDispatched"
    INSERTED_AND_ACTION_DISPATCHED = "insertedAndActionDispatched"

    @property
    def did_insert(self) -> bool:
        return self in (InsertionResult.INSERTED, InsertionResult.INSERTED_AND_ACTION_DISPATCHED)

    @property
    def did_dispatch_action(self) -> bool:
        return self in (
            InsertionResult.ACTION_DISPATCHED,
            InsertionResult.INSERTED_AND_ACTION_DISPATCHED,
        )


def can_dispatch_post_insertion_action(
    preferred_target_pid: int | None,
    required_target_pid: int | None,
    is_secure_text_field: bool,
    modifiers_released: bool,
    exact_focus_is_active: bool,
) -> bool:
    """Guard for the "press Enter after inserting" path.

    Sending a key into the wrong window, a password field, or while the
    dictation hotkey is still held would all be destructive, so every
    condition must hold.
    """
    if preferred_target_pid is None or preferred_target_pid <= 0:
        return False
    if required_target_pid != preferred_target_pid:
        return False
    return not is_secure_text_field and modifiers_released and exact_focus_is_active


def can_insert_before_post_insertion_action(
    preferred_target_pid: int | None,
    required_target_pid: int | None,
    is_secure_text_field: bool,
    exact_focus_is_active: bool,
) -> bool:
    if preferred_target_pid is None or preferred_target_pid <= 0:
        return False
    if required_target_pid != preferred_target_pid:
        return False
    return not is_secure_text_field and exact_focus_is_active


@dataclass
class TypingService:
    backend: object = field(default_factory=make_injection_backend)
    clipboard: Clipboard = field(default_factory=InMemoryClipboard)
    paste_key_name: str = "v"
    #: How long to let the target app read the clipboard before restoring it.
    paste_settle_seconds: float = 0.12
    insertion_mode: TextInsertionMode = TextInsertionMode.STANDARD

    def __post_init__(self) -> None:
        self._paste_lock = threading.Lock()

    def type_text(self, text: str, mode: TextInsertionMode | None = None) -> bool:
        if not text:
            return True
        mode = mode or self.insertion_mode
        if mode is TextInsertionMode.RELIABLE_PASTE:
            return self.insert_via_clipboard(text)
        if self.backend.type_text(text):
            return True
        # Direct typing can fail on a native Wayland client with an X11-only
        # backend; the clipboard path still reaches it.
        return self.insert_via_clipboard(text)

    def insert_via_clipboard(self, text: str) -> bool:
        if not text:
            return True
        with self._paste_lock:
            snapshot = PreservedClipboardSnapshot.capture(self.clipboard)
            if not self.clipboard.write_all([make_transient_clipboard_item(text)]):
                return False
            owned_change_count = self.clipboard.change_count
            pasted = self.send_paste_chord()
            if self.paste_settle_seconds > 0:
                time.sleep(self.paste_settle_seconds)
            snapshot.restore(self.clipboard, if_unchanged_since=owned_change_count)
            return pasted

    def send_paste_chord(self) -> bool:
        modifier = self.backend.MODIFIER_NAMES[ModifierFlags.CONTROL]
        return self.backend.send_chord(self.paste_key_name, [modifier])

    def send_key(self, key: str, modifiers: ModifierFlags = ModifierFlags.NONE) -> bool:
        names = [
            name
            for flag, name in self.backend.MODIFIER_NAMES.items()
            if ModifierFlags(modifiers).contains(flag)
        ]
        return self.backend.send_chord(key, names)
