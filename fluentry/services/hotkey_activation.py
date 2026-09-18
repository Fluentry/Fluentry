"""Turn shortcut presses into start/stop decisions.

The press-handling half of `GlobalHotkeyManager`, kept pure so the three
activation modes can be tested without an input grab:

* **toggle** — tap to start, tap again to stop,
* **hold** — record only while held,
* **automatic** — a short press toggles, a long press is push-to-talk.

The automatic mode's subtlety is that the *same* release can mean three
different things depending on what the press did: a quick tap while already
recording stops it, a quick tap that itself started the recording leaves it
running, and a quick tap that started nothing toggles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..persistence.settings_store import HotkeyActivationMode

#: A press shorter than this is a tap; longer is push-to-talk.
AUTOMATIC_TAP_THRESHOLD_SECONDS = 0.4


class ActivationAction(Enum):
    NONE = "none"
    START = "start"
    STOP = "stop"
    TOGGLE = "toggle"
    CONTINUE = "continue"


@dataclass
class AutomaticPress:
    started_at: float
    was_target_active: bool
    did_start: bool = False


@dataclass
class HotkeyActivationState:
    """One shortcut's press lifecycle."""

    mode: str = HotkeyActivationMode.TOGGLE
    tap_threshold: float = AUTOMATIC_TAP_THRESHOLD_SECONDS
    is_key_pressed: bool = False
    _press: AutomaticPress | None = field(default=None, repr=False)

    def press(self, now: float, is_recording: bool) -> ActivationAction:
        if self.is_key_pressed:
            return ActivationAction.NONE
        self.is_key_pressed = True

        if self.mode == HotkeyActivationMode.TOGGLE:
            self._press = None
            return ActivationAction.STOP if is_recording else ActivationAction.START

        if self.mode == HotkeyActivationMode.HOLD:
            self._press = None
            return ActivationAction.STOP if is_recording else ActivationAction.START

        # Automatic: begin timing, and start immediately when idle so the
        # first syllable is never lost while deciding tap-versus-hold.
        self._press = AutomaticPress(started_at=now, was_target_active=is_recording)
        if is_recording:
            return ActivationAction.NONE
        self._press.did_start = True
        return ActivationAction.START

    def release(self, now: float) -> ActivationAction:
        if not self.is_key_pressed:
            return ActivationAction.NONE
        self.is_key_pressed = False

        if self.mode == HotkeyActivationMode.TOGGLE:
            return ActivationAction.NONE
        if self.mode == HotkeyActivationMode.HOLD:
            return ActivationAction.STOP

        press = self._press
        self._press = None
        if press is None:
            return ActivationAction.NONE

        duration = max(0.0, now - press.started_at)
        if duration < self.tap_threshold:
            if press.was_target_active:
                return ActivationAction.STOP
            if press.did_start:
                return ActivationAction.CONTINUE
            return ActivationAction.TOGGLE

        if press.was_target_active or press.did_start:
            return ActivationAction.STOP
        return ActivationAction.NONE

    def modifier_only_arm(self, now: float, is_recording: bool) -> ActivationAction:
        """A modifier-only shortcut was pressed.

        In toggle mode this only *arms* the press: the recording decision
        waits for the release, because a press that turns out to be part of a
        chord must not have started anything.
        """
        if self.mode == HotkeyActivationMode.TOGGLE:
            self.is_key_pressed = True
            return ActivationAction.NONE
        return self.press(now, is_recording)

    def modifier_only_finish(self, now: float, was_clean_press: bool) -> ActivationAction:
        """A modifier-only shortcut was released.

        `was_clean_press` is false when another key or a click intervened, in
        which case the press was never a tap.
        """
        if self.mode == HotkeyActivationMode.TOGGLE:
            if not self.is_key_pressed:
                return ActivationAction.NONE
            self.is_key_pressed = False
            return ActivationAction.TOGGLE if was_clean_press else ActivationAction.NONE
        return self.release(now) if was_clean_press else self.interrupt()

    def interrupt(self) -> ActivationAction:
        """A press was cut short — by a mouse click, a lock, or a lost grab."""
        if not self.is_key_pressed:
            return ActivationAction.NONE
        self.is_key_pressed = False
        self._press = None
        from .hotkey_decision import should_force_stop_interrupted_primary_press

        return (
            ActivationAction.STOP
            if should_force_stop_interrupted_primary_press(self.mode)
            else ActivationAction.NONE
        )

    def reset(self) -> None:
        self.is_key_pressed = False
        self._press = None
