"""Reading the keyboard directly.

evdev is the only backend that sees every key on Wayland, so it is the one
that has to be right. These drive the translation layer with synthetic
kernel events — no hardware and no `input` group needed — and then run them
through the real hotkey manager, so the whole chain from key to dictation is
covered before anyone changes a system permission.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

evdev = pytest.importorskip("evdev", reason="evdev is not installed")

from fluentry.models.hotkey import HotkeyShortcut  # noqa: E402
from fluentry.models.keycodes import (  # noqa: E402
    KEY_A,
    KEY_LEFTSHIFT,
    KEY_RIGHTALT,
    ModifierFlags,
)
from fluentry.persistence.settings_store import HotkeyActivationMode  # noqa: E402
from fluentry.platform.hotkey_listener import EvdevHotkeyBackend  # noqa: E402
from fluentry.services.global_hotkey_manager import GlobalHotkeyManager  # noqa: E402
from fluentry.services.hotkey_events import InputEventType  # noqa: E402

DOWN, UP, REPEAT = 1, 0, 2


@dataclass
class FakeEvent:
    type: int
    code: int
    value: int


class FakeDevice:
    """Replays a fixed sequence the way a kernel device would."""

    def __init__(self, events) -> None:
        self.events = list(events)

    def read_loop(self):
        yield from self.events


def key(code: int, value: int) -> FakeEvent:
    return FakeEvent(evdev.ecodes.EV_KEY, code, value)


def drain(events) -> list:
    backend = EvdevHotkeyBackend()
    backend._running.set()
    seen = []
    backend._pump(FakeDevice(events), seen.append)
    return seen


# --- translating kernel events ---------------------------------------------


def test_a_modifier_press_and_release_becomes_a_flags_change():
    seen = drain([key(KEY_RIGHTALT, DOWN), key(KEY_RIGHTALT, UP)])

    assert [event.type for event in seen] == [
        InputEventType.FLAGS_CHANGED,
        InputEventType.FLAGS_CHANGED,
    ]
    assert seen[0].key_code == KEY_RIGHTALT
    assert ModifierFlags.ALT in seen[0].modifiers
    assert seen[0].pressed_modifier_key_codes == frozenset({KEY_RIGHTALT})
    assert seen[1].pressed_modifier_key_codes == frozenset()


def test_an_ordinary_key_becomes_a_key_down_and_up():
    seen = drain([key(KEY_A, DOWN), key(KEY_A, UP)])
    assert [event.type for event in seen] == [
        InputEventType.KEY_DOWN,
        InputEventType.KEY_UP,
    ]
    assert all(event.key_code == KEY_A for event in seen)


def test_auto_repeat_is_not_a_transition():
    """Holding a key repeats; that must not read as pressing it again."""
    seen = drain([key(KEY_RIGHTALT, DOWN), key(KEY_RIGHTALT, REPEAT), key(KEY_RIGHTALT, REPEAT)])
    assert len(seen) == 1


def test_modifiers_combine_while_held():
    seen = drain([key(KEY_LEFTSHIFT, DOWN), key(KEY_A, DOWN)])
    assert ModifierFlags.SHIFT in seen[1].modifiers
    assert seen[1].type is InputEventType.KEY_DOWN


def test_non_key_events_are_ignored():
    seen = drain([FakeEvent(evdev.ecodes.EV_SYN, 0, 0), key(KEY_A, DOWN)])
    assert len(seen) == 1


def test_a_mouse_button_is_reported_as_one():
    seen = drain([FakeEvent(evdev.ecodes.EV_KEY, evdev.ecodes.BTN_MOUSE, DOWN)])
    assert seen[0].mouse_button == 0
    assert seen[0].key_code == 0


def test_stopping_ends_the_pump():
    backend = EvdevHotkeyBackend()
    backend._running.clear()  # Already stopped.
    seen = []
    backend._pump(FakeDevice([key(KEY_A, DOWN)]), seen.append)
    assert seen == []


# --- the whole chain, as it will behave once permissions allow --------------


def manager_for(mode: str, clock) -> tuple[GlobalHotkeyManager, list[str]]:
    calls: list[str] = []
    running = {"value": False}

    def start(_mode) -> None:
        running["value"] = True
        calls.append("start")

    def stop(_mode) -> None:
        running["value"] = False
        calls.append("stop")

    manager = GlobalHotkeyManager(
        mode=mode,
        is_recording=lambda: running["value"],
        on_start=start,
        on_stop=stop,
        clock=clock,
    )
    manager.configure(
        primary_shortcuts=[
            HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])
        ],
        mode=mode,
    )
    return manager, calls


def test_holding_right_alt_starts_and_releasing_stops():
    """What the user asked for: push to talk on a bare modifier."""
    now = [100.0]
    manager, calls = manager_for(HotkeyActivationMode.AUTOMATIC, lambda: now[0])

    events = drain([key(KEY_RIGHTALT, DOWN)])
    for event in events:
        manager.handle_event(event)
    assert calls == ["start"]

    now[0] += 1.5  # Held well past the tap threshold.
    for event in drain([key(KEY_RIGHTALT, UP)]):
        manager.handle_event(event)
    assert calls == ["start", "stop"]


def test_tapping_right_alt_toggles_in_automatic_mode():
    now = [100.0]
    manager, calls = manager_for(HotkeyActivationMode.AUTOMATIC, lambda: now[0])

    for event in drain([key(KEY_RIGHTALT, DOWN)]):
        manager.handle_event(event)
    now[0] += 0.05  # A tap.
    for event in drain([key(KEY_RIGHTALT, UP)]):
        manager.handle_event(event)
    assert calls == ["start"], "a tap should leave it recording"

    now[0] += 2.0
    for event in drain([key(KEY_RIGHTALT, DOWN)]):
        manager.handle_event(event)
    now[0] += 0.05
    for event in drain([key(KEY_RIGHTALT, UP)]):
        manager.handle_event(event)
    assert calls == ["start", "stop"]


def test_an_unrelated_key_does_not_trigger_dictation():
    now = [100.0]
    manager, calls = manager_for(HotkeyActivationMode.AUTOMATIC, lambda: now[0])
    for event in drain([key(KEY_A, DOWN), key(KEY_A, UP)]):
        manager.handle_event(event)
    assert calls == []


# --- availability -----------------------------------------------------------


def test_evdev_is_unavailable_without_readable_devices(monkeypatch):
    """No `input` group membership means no readable devices, and the app
    must report that rather than pretending to listen."""
    monkeypatch.setattr(EvdevHotkeyBackend, "readable_device_paths", staticmethod(lambda: []))
    assert EvdevHotkeyBackend.is_available() is False


def test_evdev_is_available_once_a_device_can_be_read(monkeypatch):
    monkeypatch.setattr(
        EvdevHotkeyBackend, "readable_device_paths", staticmethod(lambda: ["/dev/input/event0"])
    )
    assert EvdevHotkeyBackend.is_available() is True
