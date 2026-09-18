"""Global hotkey listening.

Replaces the macOS `CGEventTap`, which needed Input Monitoring permission.
Linux offers two practical paths and they have different reach:

============  ==========================  ==============================
Backend       Sees                        Requires
============  ==========================  ==============================
`evdev`       every key, on any session   membership of the `input` group
`pynput`      X11 and XWayland clients    an X display (`DISPLAY`)
============  ==========================  ==============================

Both are normalized into the same event stream the pure decision machine in
`hotkey_decision` already consumes, so the tested behaviour is identical no
matter which backend is active.
"""

from __future__ import annotations

import glob
import os
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from ..models.keycodes import MODIFIER_KEY_FLAGS, ModifierFlags
from ..services.hotkey_events import InputEventType


@dataclass(frozen=True)
class InputEvent:
    type: InputEventType
    key_code: int = 0
    modifiers: ModifierFlags = ModifierFlags.NONE
    pressed_modifier_key_codes: frozenset[int] = frozenset()
    mouse_button: int | None = None


EventHandler = Callable[[InputEvent], None]


class HotkeyBackend(Protocol):
    name: str

    def start(self, handler: EventHandler) -> None: ...

    def stop(self) -> None: ...

    @property
    def is_running(self) -> bool: ...


class _ModifierTracker:
    """Keeps the pressed-modifier set the decision machine needs."""

    def __init__(self) -> None:
        self._pressed: set[int] = set()

    def update(self, key_code: int, is_down: bool) -> tuple[ModifierFlags, frozenset[int]]:
        if key_code in MODIFIER_KEY_FLAGS:
            if is_down:
                self._pressed.add(key_code)
            else:
                self._pressed.discard(key_code)
        flags = ModifierFlags.NONE
        for code in self._pressed:
            flag = MODIFIER_KEY_FLAGS.get(code)
            if flag is not None:
                flags = flags.union(flag)
        return flags, frozenset(self._pressed)

    @property
    def flags(self) -> ModifierFlags:
        flags = ModifierFlags.NONE
        for code in self._pressed:
            flag = MODIFIER_KEY_FLAGS.get(code)
            if flag is not None:
                flags = flags.union(flag)
        return flags

    @property
    def pressed(self) -> frozenset[int]:
        return frozenset(self._pressed)

    def reset(self) -> None:
        self._pressed.clear()


class SyntheticHotkeyBackend:
    """Test and headless backend: events are pushed in by the caller."""

    name = "synthetic"

    def __init__(self) -> None:
        self._handler: EventHandler | None = None
        self.tracker = _ModifierTracker()

    @staticmethod
    def is_available() -> bool:
        return True

    def start(self, handler: EventHandler) -> None:
        self._handler = handler

    def stop(self) -> None:
        self._handler = None
        self.tracker.reset()

    @property
    def is_running(self) -> bool:
        return self._handler is not None

    def emit(self, event: InputEvent) -> None:
        if self._handler is not None:
            self._handler(event)

    def press_key(self, key_code: int) -> None:
        modifiers, pressed = self.tracker.update(key_code, is_down=True)
        event_type = (
            InputEventType.FLAGS_CHANGED if key_code in MODIFIER_KEY_FLAGS else InputEventType.KEY_DOWN
        )
        self.emit(InputEvent(event_type, key_code, modifiers, pressed))

    def release_key(self, key_code: int) -> None:
        modifiers, pressed = self.tracker.update(key_code, is_down=False)
        event_type = (
            InputEventType.FLAGS_CHANGED if key_code in MODIFIER_KEY_FLAGS else InputEventType.KEY_UP
        )
        self.emit(InputEvent(event_type, key_code, modifiers, pressed))

    def click(self, button: int) -> None:
        down = {
            0: InputEventType.LEFT_MOUSE_DOWN,
            1: InputEventType.RIGHT_MOUSE_DOWN,
        }.get(button, InputEventType.OTHER_MOUSE_DOWN)
        up = {
            0: InputEventType.LEFT_MOUSE_UP,
            1: InputEventType.RIGHT_MOUSE_UP,
        }.get(button, InputEventType.OTHER_MOUSE_UP)
        self.emit(InputEvent(down, modifiers=self.tracker.flags, mouse_button=button))
        self.emit(InputEvent(up, modifiers=self.tracker.flags, mouse_button=button))


class EvdevHotkeyBackend:
    """Reads keyboards directly. Sees every key, on X11 and Wayland alike."""

    name = "evdev"

    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._devices: list = []
        self._running = threading.Event()
        self.tracker = _ModifierTracker()

    @staticmethod
    def readable_device_paths() -> list[str]:
        return [
            path for path in sorted(glob.glob("/dev/input/event*")) if os.access(path, os.R_OK)
        ]

    @staticmethod
    def is_available() -> bool:
        try:
            import evdev  # noqa: F401
        except Exception:
            return False
        return bool(EvdevHotkeyBackend.readable_device_paths())

    def start(self, handler: EventHandler) -> None:
        import evdev

        self._running.set()
        for path in self.readable_device_paths():
            try:
                device = evdev.InputDevice(path)
            except OSError:
                continue
            capabilities = device.capabilities()
            if evdev.ecodes.EV_KEY not in capabilities:
                device.close()
                continue
            self._devices.append(device)
            thread = threading.Thread(
                target=self._pump, args=(device, handler), name=f"fluentry.hotkey.{path}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def _pump(self, device, handler: EventHandler) -> None:
        import evdev

        try:
            for event in device.read_loop():
                if not self._running.is_set():
                    return
                if event.type != evdev.ecodes.EV_KEY:
                    continue
                # value 2 is auto-repeat, which is not a transition.
                if event.value not in (0, 1):
                    continue
                is_down = event.value == 1
                code = event.code
                if evdev.ecodes.BTN_MOUSE <= code <= evdev.ecodes.BTN_TASK:
                    button = code - evdev.ecodes.BTN_MOUSE
                    handler(
                        InputEvent(
                            self._mouse_event_type(button, is_down),
                            modifiers=self.tracker.flags,
                            pressed_modifier_key_codes=self.tracker.pressed,
                            mouse_button=button,
                        )
                    )
                    continue
                modifiers, pressed = self.tracker.update(code, is_down)
                if code in MODIFIER_KEY_FLAGS:
                    event_type = InputEventType.FLAGS_CHANGED
                else:
                    event_type = InputEventType.KEY_DOWN if is_down else InputEventType.KEY_UP
                handler(InputEvent(event_type, code, modifiers, pressed))
        except OSError:
            # The device was unplugged; the remaining keyboards keep working.
            return

    @staticmethod
    def _mouse_event_type(button: int, is_down: bool) -> InputEventType:
        if button == 0:
            return InputEventType.LEFT_MOUSE_DOWN if is_down else InputEventType.LEFT_MOUSE_UP
        if button == 1:
            return InputEventType.RIGHT_MOUSE_DOWN if is_down else InputEventType.RIGHT_MOUSE_UP
        return InputEventType.OTHER_MOUSE_DOWN if is_down else InputEventType.OTHER_MOUSE_UP

    def stop(self) -> None:
        self._running.clear()
        for device in self._devices:
            try:
                device.close()
            except Exception:
                pass
        self._devices.clear()
        self._threads.clear()
        self.tracker.reset()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()


#: pynput reports keys by name/vk; these map onto the evdev codes a shortcut stores.
_PYNPUT_SPECIAL_KEYS = {
    "alt": 56, "alt_l": 56, "alt_r": 100, "alt_gr": 100,
    "ctrl": 29, "ctrl_l": 29, "ctrl_r": 97,
    "shift": 42, "shift_l": 42, "shift_r": 54,
    "cmd": 125, "cmd_l": 125, "cmd_r": 126,
    "esc": 1, "space": 57, "tab": 15, "enter": 28, "backspace": 14,
    "delete": 111, "up": 103, "down": 108, "left": 105, "right": 106,
    "home": 102, "end": 107, "page_up": 104, "page_down": 109, "insert": 110,
    "caps_lock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

_PYNPUT_CHARACTER_KEYS = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11, "-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41,
    "\\": 43, ",": 51, ".": 52, "/": 53,
}


def evdev_code_for_pynput_key(key) -> int | None:
    name = getattr(key, "name", None)
    if isinstance(name, str):
        code = _PYNPUT_SPECIAL_KEYS.get(name)
        if code is not None:
            return code
    character = getattr(key, "char", None)
    if isinstance(character, str) and character:
        return _PYNPUT_CHARACTER_KEYS.get(character.lower())
    return None


class PynputHotkeyBackend:
    """X11/XWayland listener. No extra permission, but Wayland-native
    clients' keystrokes are invisible to it."""

    name = "pynput"

    def __init__(self) -> None:
        self._keyboard_listener = None
        self._mouse_listener = None
        self.tracker = _ModifierTracker()

    @staticmethod
    def is_available() -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        try:
            from pynput import keyboard  # noqa: F401
        except Exception:
            return False
        return True

    def start(self, handler: EventHandler) -> None:
        from pynput import keyboard, mouse

        def on_press(key) -> None:
            code = evdev_code_for_pynput_key(key)
            if code is None:
                return
            modifiers, pressed = self.tracker.update(code, is_down=True)
            event_type = (
                InputEventType.FLAGS_CHANGED
                if code in MODIFIER_KEY_FLAGS
                else InputEventType.KEY_DOWN
            )
            handler(InputEvent(event_type, code, modifiers, pressed))

        def on_release(key) -> None:
            code = evdev_code_for_pynput_key(key)
            if code is None:
                return
            modifiers, pressed = self.tracker.update(code, is_down=False)
            event_type = (
                InputEventType.FLAGS_CHANGED
                if code in MODIFIER_KEY_FLAGS
                else InputEventType.KEY_UP
            )
            handler(InputEvent(event_type, code, modifiers, pressed))

        def on_click(x, y, button, pressed) -> None:
            index = {"left": 0, "right": 1, "middle": 2}.get(button.name, 3)
            event_type = EvdevHotkeyBackend._mouse_event_type(index, pressed)
            handler(
                InputEvent(
                    event_type,
                    modifiers=self.tracker.flags,
                    pressed_modifier_key_codes=self.tracker.pressed,
                    mouse_button=index,
                )
            )

        self._keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._keyboard_listener.start()
        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._mouse_listener.start()

    def stop(self) -> None:
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._keyboard_listener = None
        self._mouse_listener = None
        self.tracker.reset()

    @property
    def is_running(self) -> bool:
        return self._keyboard_listener is not None


def available_hotkey_backends() -> list[str]:
    names = []
    if EvdevHotkeyBackend.is_available():
        names.append(EvdevHotkeyBackend.name)
    if PynputHotkeyBackend.is_available():
        names.append(PynputHotkeyBackend.name)
    return names


def make_hotkey_backend() -> HotkeyBackend:
    """evdev when readable (it sees every app), pynput otherwise."""
    if EvdevHotkeyBackend.is_available():
        return EvdevHotkeyBackend()
    if PynputHotkeyBackend.is_available():
        return PynputHotkeyBackend()
    return SyntheticHotkeyBackend()
