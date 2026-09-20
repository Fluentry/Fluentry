"""Insert dictated text into whatever application has focus.

`TypingService`, retargeted at Linux input synthesis.

Other desktops have a single mechanism gated behind
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

from ..logging_setup import get_logger
from ..models import keycodes
from ..models.keycodes import ModifierFlags
from ..persistence.settings_types import TextInsertionMode
from .clipboard import TEXT_MIME, Clipboard, InMemoryClipboard, PreservedClipboardSnapshot

_log = get_logger("inject")

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
    except subprocess.TimeoutExpired:
        # Typing a long transcript can outrun the timeout; the text is then
        # half-inserted, which looks exactly like a silent failure.
        _log.error("%s timed out after %.0fs", command[0], timeout)
        return False
    except (OSError, subprocess.SubprocessError) as error:
        _log.error("%s could not be run: %s", command[0], error)
        return False
    if result.returncode != 0:
        _log.error(
            "%s exited %d: %s",
            command[0],
            result.returncode,
            (result.stderr or b"").decode("utf-8", "replace").strip()[:400],
        )
        return False
    _log.debug("ran %s", " ".join(command[:3]))
    return True


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


#: ydotool takes raw evdev keycodes, not key names, and anything it cannot
#: parse it treats as a pause — while still exiting 0. A name that is missing
#: here therefore has to fail before it reaches the command line, or the
#: caller is told the keystroke landed when nothing was sent at all.
YDOTOOL_KEYCODES: dict[str, int] = {
    "ctrl": keycodes.KEY_LEFTCTRL,
    "control": keycodes.KEY_LEFTCTRL,
    "shift": keycodes.KEY_LEFTSHIFT,
    "alt": keycodes.KEY_LEFTALT,
    "super": keycodes.KEY_LEFTMETA,
    "meta": keycodes.KEY_LEFTMETA,
    "return": keycodes.KEY_ENTER,
    "enter": keycodes.KEY_ENTER,
    "tab": keycodes.KEY_TAB,
    "escape": keycodes.KEY_ESC,
    "esc": keycodes.KEY_ESC,
    "space": keycodes.KEY_SPACE,
    "backspace": keycodes.KEY_BACKSPACE,
    **{name.lower(): code for code, name in keycodes.QWERTY_FALLBACK.items()},
}


def ydotool_keycode(name: str) -> int | None:
    return YDOTOOL_KEYCODES.get(name.strip().lower())


#: Every modifier, both sides. xdotool has `--clearmodifiers` for this;
#: ydotool has no equivalent, so the release has to be sent by hand. Without
#: it a still-held dictation hotkey turns each injected character into a
#: shortcut and nothing reaches the document — and the default hotkey is
#: Right Alt, which is AltGr on most layouts outside the US.
CLEARED_MODIFIER_CODES = (
    keycodes.KEY_LEFTCTRL,
    keycodes.KEY_RIGHTCTRL,
    keycodes.KEY_LEFTSHIFT,
    keycodes.KEY_RIGHTSHIFT,
    keycodes.KEY_LEFTALT,
    keycodes.KEY_RIGHTALT,
    keycodes.KEY_LEFTMETA,
    keycodes.KEY_RIGHTMETA,
)


class YdotoolBackend:
    name = "ydotool"

    MODIFIER_NAMES = {
        ModifierFlags.CONTROL: "ctrl",
        ModifierFlags.SHIFT: "shift",
        ModifierFlags.ALT: "alt",
        ModifierFlags.SUPER: "super",
    }

    #: ydotool's own default. Pushing it lower drops characters: the events
    #: reach the kernel either way, but a compositor coalescing them that
    #: fast loses some on the way to the focused window.
    KEY_DELAY_MILLISECONDS = 20

    #: Gap between the events of a chord, so the modifier is applied before
    #: the key it modifies arrives.
    CHORD_DELAY_MILLISECONDS = 25

    @staticmethod
    def is_available() -> bool:
        return shutil.which("ydotool") is not None

    def clear_modifiers(self) -> bool:
        """xdotool's `--clearmodifiers`, which ydotool does not provide."""
        return _run(["ydotool", "key", *(f"{code}:0" for code in CLEARED_MODIFIER_CODES)])

    def type_text(self, text: str) -> bool:
        if not text:
            return True
        self.clear_modifiers()
        return _run(
            [
                "ydotool",
                "type",
                "--key-delay",
                str(self.KEY_DELAY_MILLISECONDS),
                "--",
                text,
            ]
        )

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        codes = [ydotool_keycode(name) for name in (*modifiers, key)]
        if any(code is None for code in codes):
            return False
        self.clear_modifiers()
        # Press the modifiers in order, then release everything in reverse, so
        # the chord is never left with a modifier stuck down.
        #
        # The delay matters as much as the order: with none, Ctrl-down and
        # V-down land in the same millisecond, and a compositor that has not
        # applied the modifier yet sees a bare "v". No real keyboard produces
        # a chord that fast.
        presses = [f"{code}:1" for code in codes]
        releases = [f"{code}:0" for code in reversed(codes)]
        return _run(
            [
                "ydotool",
                "key",
                "--key-delay",
                str(self.CHORD_DELAY_MILLISECONDS),
                *presses,
                *releases,
            ]
        )


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
    from .libei_injection import LibeiBackend

    return [
        backend.name
        for backend in (XdotoolBackend(), YdotoolBackend(), WtypeBackend(), LibeiBackend())
        if type(backend).is_available()
    ]


def make_injection_backend(session_type: str | None = None):
    """Pick the backend that can reach the most applications on this session.

    On Wayland, `xdotool` only reaches XWayland clients, so a native-Wayland
    backend is preferred when one is installed.
    """
    from .system_capabilities import SESSION_WAYLAND, session_type as detect_session

    from .libei_injection import LibeiBackend

    session = session_type or detect_session()
    if session == SESSION_WAYLAND:
        # libei first: it is the only one the compositor is obliged to
        # deliver. ydotool's events reach the kernel and are then dropped on
        # the way to the focused window, which looks identical to success.
        order = (LibeiBackend, YdotoolBackend, WtypeBackend, XdotoolBackend)
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
    paste_settle_seconds: float = 0.15
    #: Whether to put the user's previous clipboard back afterwards. On by
    #: default, which is the documented behaviour: the app borrows the
    #: clipboard and gives it back. Turning it off keeps the transcript
    #: there instead, which is what you want while the paste itself is
    #: unreliable - a user whose paste silently failed is otherwise left
    #: with nothing at all.
    restore_clipboard_after_paste: bool = True
    #: How long to let a clipboard write reach the compositor before the
    #: paste chord asks for it. `wl-copy` has already taken the selection by
    #: the time it returns, so this only needs to cover the handover.
    clipboard_settle_seconds: float = 0.05
    insertion_mode: TextInsertionMode = TextInsertionMode.STANDARD

    def __post_init__(self) -> None:
        self._paste_lock = threading.Lock()

    def type_text(
        self, text: str, mode: TextInsertionMode | None = None, into_terminal: bool = False
    ) -> bool:
        if not text:
            return True
        mode = mode or self.insertion_mode
        _log.info(
            "inserting %d chars via %s, mode=%s%s",
            len(text),
            self.backend.name,
            mode.value,
            " (terminal)" if into_terminal else "",
        )
        if mode is TextInsertionMode.RELIABLE_PASTE:
            return self.insert_via_clipboard(text, into_terminal=into_terminal)
        if self.backend.type_text(text):
            _log.info("typed directly")
            return True
        # Direct typing can fail on a native Wayland client with an X11-only
        # backend; the clipboard path still reaches it.
        _log.warning("direct typing failed, falling back to the clipboard")
        inserted = self.insert_via_clipboard(text, into_terminal=into_terminal)
        _log.info("clipboard fallback %s", "succeeded" if inserted else "FAILED")
        return inserted

    def insert_via_clipboard(self, text: str, into_terminal: bool = False) -> bool:
        if not text:
            return True
        with self._paste_lock:
            snapshot = PreservedClipboardSnapshot.capture(self.clipboard)
            if not self.clipboard.write_all([make_transient_clipboard_item(text)]):
                return False
            owned_change_count = self.clipboard.change_count
            if self.clipboard_settle_seconds > 0:
                time.sleep(self.clipboard_settle_seconds)
            pasted = self.send_paste_chord(into_terminal=into_terminal)
            _log.info("paste chord %s", "sent" if pasted else "FAILED")
            if not self.restore_clipboard_after_paste:
                _log.info("left the transcript on the clipboard")
                return pasted
            if self.paste_settle_seconds > 0:
                time.sleep(self.paste_settle_seconds)
            snapshot.restore(self.clipboard, if_unchanged_since=owned_change_count)
            return pasted

    def send_paste_chord(self, into_terminal: bool = False) -> bool:
        modifiers = [self.backend.MODIFIER_NAMES[ModifierFlags.CONTROL]]
        if into_terminal:
            # A terminal reads Ctrl+V as quoted-insert and shows a literal
            # ^V; Ctrl+Shift+V is its paste. This is why the focused window
            # has to be known - see the Fluentry Focus GNOME extension.
            modifiers.append(self.backend.MODIFIER_NAMES[ModifierFlags.SHIFT])
        return self.backend.send_chord(self.paste_key_name, modifiers)

    def send_key(self, key: str, modifiers: ModifierFlags = ModifierFlags.NONE) -> bool:
        names = [
            name
            for flag, name in self.backend.MODIFIER_NAMES.items()
            if ModifierFlags(modifiers).contains(flag)
        ]
        return self.backend.send_chord(key, names)
