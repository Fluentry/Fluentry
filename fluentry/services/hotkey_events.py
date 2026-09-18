"""Input event kinds and subscription masks.

The macOS build subscribed to a `CGEventMask`. On Linux the listener sits on
evdev (or an X11 grab), but the *policy* is identical and worth keeping
testable: the keyboard listener must never see mouse events, the mouse
observer must only watch button-downs, and a mouse shortcut mask must only
cover the buttons actually configured.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable


class InputEventType(IntEnum):
    KEY_DOWN = 0
    KEY_UP = 1
    FLAGS_CHANGED = 2
    LEFT_MOUSE_DOWN = 3
    LEFT_MOUSE_UP = 4
    RIGHT_MOUSE_DOWN = 5
    RIGHT_MOUSE_UP = 6
    OTHER_MOUSE_DOWN = 7
    OTHER_MOUSE_UP = 8


def mask_bit(event_type: InputEventType) -> int:
    return 1 << int(event_type)


def mask_contains(mask: int, event_type: InputEventType) -> bool:
    return mask & mask_bit(event_type) != 0


def keyboard_event_mask() -> int:
    """Keyboard listener subscription: key transitions and modifier changes only."""
    return (
        mask_bit(InputEventType.KEY_DOWN)
        | mask_bit(InputEventType.KEY_UP)
        | mask_bit(InputEventType.FLAGS_CHANGED)
    )


def mouse_observer_event_mask() -> int:
    """Passive observer used to interrupt modifier-only presses: downs only."""
    return (
        mask_bit(InputEventType.LEFT_MOUSE_DOWN)
        | mask_bit(InputEventType.RIGHT_MOUSE_DOWN)
        | mask_bit(InputEventType.OTHER_MOUSE_DOWN)
    )


def mouse_shortcut_event_mask(mouse_buttons: Iterable[int]) -> int:
    """Down+up for exactly the button families a shortcut is configured on."""
    buttons = set(mouse_buttons)
    mask = 0
    if 0 in buttons:
        mask |= mask_bit(InputEventType.LEFT_MOUSE_DOWN) | mask_bit(InputEventType.LEFT_MOUSE_UP)
    if 1 in buttons:
        mask |= mask_bit(InputEventType.RIGHT_MOUSE_DOWN) | mask_bit(InputEventType.RIGHT_MOUSE_UP)
    if any(button >= 2 for button in buttons):
        mask |= mask_bit(InputEventType.OTHER_MOUSE_DOWN) | mask_bit(InputEventType.OTHER_MOUSE_UP)
    return mask


def session_is_locked(session_info: dict) -> bool:
    """Screen-lock check.

    macOS read `CGSSessionScreenIsLocked`; on Linux the equivalent fact comes
    from logind's `LockedHint` property. Both keys are accepted so a caller can
    hand through whichever the platform provided.
    """
    for key in ("LockedHint", "CGSSessionScreenIsLocked"):
        value = session_info.get(key)
        if isinstance(value, bool):
            return value
    return False
