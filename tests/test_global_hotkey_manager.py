"""Activation-mode and end-to-end hotkey behaviour."""

import pytest

from fluentry.models.hotkey import HotkeyShortcut
from fluentry.models.keycodes import (
    KEY_ESC,
    KEY_LEFTALT,
    KEY_LEFTSHIFT,
    KEY_R,
    KEY_RIGHTALT,
    ModifierFlags,
)
from fluentry.persistence.settings_store import HotkeyActivationMode
from fluentry.platform.hotkey_listener import (
    InputEvent,
    SyntheticHotkeyBackend,
    evdev_code_for_pynput_key,
)
from fluentry.services.hotkey_activation import (
    AUTOMATIC_TAP_THRESHOLD_SECONDS,
    ActivationAction,
    HotkeyActivationState,
)
from fluentry.services.global_hotkey_manager import GlobalHotkeyManager
from fluentry.services.hotkey_decision import HoldModeType
from fluentry.services.hotkey_events import InputEventType, mask_contains


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    """Stands in for the ASR service."""

    def __init__(self) -> None:
        self.running = False
        self.events: list[str] = []

    def is_recording(self) -> bool:
        return self.running

    def start(self, _mode) -> None:
        self.running = True
        self.events.append("start")

    def stop(self, _mode) -> None:
        self.running = False
        self.events.append("stop")


def make_manager(shortcut: HotkeyShortcut, mode: str, clock: Clock | None = None, **kwargs):
    clock = clock or Clock()
    recorder = Recorder()
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(
        backend=backend,
        mode=mode,
        is_recording=recorder.is_recording,
        on_start=recorder.start,
        on_stop=recorder.stop,
        clock=clock,
        **kwargs,
    )
    manager.configure([shortcut], mode=mode)
    manager.start()
    return manager, backend, recorder, clock


# --- activation state machine -----------------------------------------------


def test_toggle_mode_starts_and_stops_on_alternating_taps():
    state = HotkeyActivationState(mode=HotkeyActivationMode.TOGGLE)

    assert state.press(now=0, is_recording=False) is ActivationAction.START
    assert state.release(now=0.1) is ActivationAction.NONE
    assert state.press(now=1, is_recording=True) is ActivationAction.STOP
    assert state.release(now=1.1) is ActivationAction.NONE


def test_hold_mode_records_only_while_held():
    state = HotkeyActivationState(mode=HotkeyActivationMode.HOLD)

    assert state.press(now=0, is_recording=False) is ActivationAction.START
    assert state.release(now=2) is ActivationAction.STOP


def test_automatic_mode_short_tap_from_idle_starts_and_keeps_recording():
    state = HotkeyActivationState(mode=HotkeyActivationMode.AUTOMATIC)

    assert state.press(now=0, is_recording=False) is ActivationAction.START
    # Released inside the tap window, and this press is what started it, so
    # recording continues — the user tapped to begin dictating.
    assert state.release(now=0.1) is ActivationAction.CONTINUE


def test_automatic_mode_short_tap_while_recording_stops():
    state = HotkeyActivationState(mode=HotkeyActivationMode.AUTOMATIC)

    assert state.press(now=0, is_recording=True) is ActivationAction.NONE
    assert state.release(now=0.1) is ActivationAction.STOP


def test_automatic_mode_long_press_is_push_to_talk():
    state = HotkeyActivationState(mode=HotkeyActivationMode.AUTOMATIC)

    assert state.press(now=0, is_recording=False) is ActivationAction.START
    assert state.release(now=AUTOMATIC_TAP_THRESHOLD_SECONDS + 0.1) is ActivationAction.STOP


def test_automatic_mode_ignores_a_release_with_no_matching_press():
    state = HotkeyActivationState(mode=HotkeyActivationMode.AUTOMATIC)
    assert state.release(now=1) is ActivationAction.NONE


def test_repeat_presses_while_held_are_ignored():
    state = HotkeyActivationState(mode=HotkeyActivationMode.HOLD)
    assert state.press(now=0, is_recording=False) is ActivationAction.START
    assert state.press(now=0.05, is_recording=True) is ActivationAction.NONE


def test_interrupting_a_press_stops_hold_and_automatic_but_not_toggle():
    for mode in (HotkeyActivationMode.HOLD, HotkeyActivationMode.AUTOMATIC):
        state = HotkeyActivationState(mode=mode)
        state.press(now=0, is_recording=False)
        assert state.interrupt() is ActivationAction.STOP, mode

    toggle = HotkeyActivationState(mode=HotkeyActivationMode.TOGGLE)
    toggle.press(now=0, is_recording=False)
    assert toggle.interrupt() is ActivationAction.NONE
    assert toggle.interrupt() is ActivationAction.NONE, "already released"


# --- manager: keyboard shortcuts --------------------------------------------


def test_toggle_shortcut_drives_recording_end_to_end():
    shortcut = HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.press_key(KEY_LEFTALT)
    backend.press_key(KEY_R)
    backend.release_key(KEY_R)
    assert recorder.events == ["start"]
    assert recorder.running

    backend.press_key(KEY_R)
    backend.release_key(KEY_R)
    assert recorder.events == ["start", "stop"]
    assert not recorder.running


def test_hold_shortcut_records_only_while_held():
    shortcut = HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.HOLD)

    backend.press_key(KEY_LEFTALT)
    backend.press_key(KEY_R)
    assert recorder.running
    backend.release_key(KEY_R)
    assert not recorder.running


def test_a_shortcut_with_the_wrong_modifiers_does_nothing():
    shortcut = HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.press_key(KEY_R)  # no Alt held
    backend.release_key(KEY_R)
    assert recorder.events == []


# --- manager: modifier-only shortcuts ---------------------------------------


def test_modifier_only_tap_starts_recording():
    shortcut = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.press_key(KEY_RIGHTALT)
    backend.release_key(KEY_RIGHTALT)

    assert recorder.events == ["start"]


def test_modifier_only_shortcut_ignores_a_chord():
    shortcut = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.press_key(KEY_RIGHTALT)
    backend.press_key(KEY_LEFTSHIFT)
    backend.press_key(KEY_R)
    backend.release_key(KEY_R)
    backend.release_key(KEY_LEFTSHIFT)
    backend.release_key(KEY_RIGHTALT)

    assert recorder.events == [], "Alt+Shift+R must not read as an Alt tap"


def test_a_click_during_a_modifier_press_cancels_the_tap():
    shortcut = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.HOLD)

    backend.press_key(KEY_RIGHTALT)
    assert recorder.running, "hold mode starts on press"
    backend.click(0)
    backend.release_key(KEY_RIGHTALT)

    assert recorder.events == ["start", "stop"]
    assert not recorder.running


# --- manager: mouse shortcuts -----------------------------------------------


def test_mouse_shortcut_toggles_recording():
    shortcut = HotkeyShortcut.mouse(3, ModifierFlags.NONE)
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.click(3)
    assert recorder.events == ["start"]
    backend.click(3)
    assert recorder.events == ["start", "stop"]


def test_an_unmodified_left_click_is_never_a_shortcut():
    shortcut = HotkeyShortcut.mouse(0, ModifierFlags.NONE)
    manager, backend, recorder, _ = make_manager(shortcut, HotkeyActivationMode.TOGGLE)

    backend.click(0)
    assert recorder.events == []


def test_mouse_event_mask_covers_only_configured_buttons():
    manager, backend, recorder, _ = make_manager(
        HotkeyShortcut.mouse(3, ModifierFlags.NONE), HotkeyActivationMode.TOGGLE
    )
    mask = manager.mouse_event_mask
    assert mask_contains(mask, InputEventType.OTHER_MOUSE_DOWN)
    assert not mask_contains(mask, InputEventType.LEFT_MOUSE_DOWN)


# --- manager: auxiliary shortcuts -------------------------------------------


def test_cancel_shortcut_invokes_the_cancel_callback():
    cancelled: list[bool] = []
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(backend=backend, on_cancel=lambda: cancelled.append(True))
    manager.configure(
        [HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)],
        cancel_shortcut=HotkeyShortcut.keyboard(KEY_ESC),
    )
    manager.start()

    backend.press_key(KEY_ESC)
    assert cancelled == [True]


def test_paste_last_shortcut_fires_only_when_enabled():
    pasted: list[bool] = []
    backend = SyntheticHotkeyBackend()
    manager = GlobalHotkeyManager(backend=backend, on_paste_last=lambda: pasted.append(True))
    manager.configure(
        [HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT)],
        paste_last_shortcut=HotkeyShortcut.mouse(3, ModifierFlags.NONE),
        paste_last_enabled=False,
    )
    manager.start()

    backend.click(3)
    assert pasted == []

    manager.paste_last_enabled = True
    backend.click(3)
    assert pasted == [True]


def test_a_locked_screen_ignores_every_shortcut():
    locked = [True]
    backend = SyntheticHotkeyBackend()
    recorder = Recorder()
    manager = GlobalHotkeyManager(
        backend=backend,
        mode=HotkeyActivationMode.TOGGLE,
        is_recording=recorder.is_recording,
        on_start=recorder.start,
        on_stop=recorder.stop,
        session_is_locked=lambda: locked[0],
    )
    manager.configure([HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])])
    manager.start()

    backend.press_key(KEY_RIGHTALT)
    backend.release_key(KEY_RIGHTALT)
    assert recorder.events == []

    locked[0] = False
    backend.press_key(KEY_RIGHTALT)
    backend.release_key(KEY_RIGHTALT)
    assert recorder.events == ["start"]


def test_summary_describes_the_configured_shortcuts():
    manager, *_ = make_manager(
        HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT]),
        HotkeyActivationMode.AUTOMATIC,
    )
    summary = manager.summary()
    assert summary.startswith("mode=automatic")
    assert "primary[0]=Right Alt" in summary


# --- backend key mapping ----------------------------------------------------


def test_pynput_keys_map_onto_evdev_codes():
    class NamedKey:
        def __init__(self, name):
            self.name = name

    class CharacterKey:
        def __init__(self, char):
            self.char = char
            self.name = None

    assert evdev_code_for_pynput_key(NamedKey("alt_r")) == KEY_RIGHTALT
    assert evdev_code_for_pynput_key(NamedKey("esc")) == KEY_ESC
    assert evdev_code_for_pynput_key(CharacterKey("R")) == KEY_R
    assert evdev_code_for_pynput_key(CharacterKey("€")) is None


def test_synthetic_backend_tracks_pressed_modifiers():
    backend = SyntheticHotkeyBackend()
    seen: list[InputEvent] = []
    backend.start(seen.append)

    backend.press_key(KEY_LEFTALT)
    backend.press_key(KEY_LEFTSHIFT)
    assert seen[-1].modifiers == ModifierFlags.ALT | ModifierFlags.SHIFT
    assert seen[-1].pressed_modifier_key_codes == frozenset({KEY_LEFTALT, KEY_LEFTSHIFT})

    backend.release_key(KEY_LEFTSHIFT)
    assert seen[-1].modifiers == ModifierFlags.ALT
    backend.stop()
    assert not backend.is_running
