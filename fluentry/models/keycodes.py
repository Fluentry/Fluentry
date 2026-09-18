"""Linux input keycodes and modifier flags.

The macOS original keyed everything off Carbon virtual key codes. On Linux the
only identifier that survives both X11 and Wayland is the kernel `evdev` code,
so that is what a shortcut stores. Modifier *flag* raw values deliberately keep
the macOS bit positions so serialized settings and backups stay comparable
across the two implementations.
"""

from __future__ import annotations

from enum import IntFlag


class ModifierFlags(IntFlag):
    """Modifier bitmask. Raw values mirror the macOS build so backups round-trip."""

    NONE = 0
    CAPS_LOCK = 1 << 16
    SHIFT = 1 << 17
    CONTROL = 1 << 18
    ALT = 1 << 19  # macOS "option"
    SUPER = 1 << 20  # macOS "command"
    NUMERIC_PAD = 1 << 21
    HELP = 1 << 22
    FUNCTION = 1 << 23

    def contains(self, other: "ModifierFlags") -> bool:
        return (self & other) == other

    def intersection(self, other: "ModifierFlags") -> "ModifierFlags":
        return ModifierFlags(self & other)

    def union(self, other: "ModifierFlags") -> "ModifierFlags":
        return ModifierFlags(self | other)

    def subtracting(self, other: "ModifierFlags") -> "ModifierFlags":
        return ModifierFlags(self & ~other)

    def is_subset(self, other: "ModifierFlags") -> bool:
        return (self & other) == self

    @property
    def is_empty(self) -> bool:
        return int(self) == 0


# --- evdev key codes (subset used by the app) -------------------------------

KEY_ESC = 1
KEY_1 = 2
KEY_2 = 3
KEY_3 = 4
KEY_4 = 5
KEY_5 = 6
KEY_6 = 7
KEY_7 = 8
KEY_8 = 9
KEY_9 = 10
KEY_0 = 11
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_Q = 16
KEY_W = 17
KEY_E = 18
KEY_R = 19
KEY_T = 20
KEY_Y = 21
KEY_U = 22
KEY_I = 23
KEY_O = 24
KEY_P = 25
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A = 30
KEY_S = 31
KEY_D = 32
KEY_F = 33
KEY_G = 34
KEY_H = 35
KEY_J = 36
KEY_K = 37
KEY_L = 38
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_LEFTSHIFT = 42
KEY_BACKSLASH = 43
KEY_Z = 44
KEY_X = 45
KEY_C = 46
KEY_V = 47
KEY_B = 48
KEY_N = 49
KEY_M = 50
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_KPASTERISK = 55
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_F1 = 59
KEY_F2 = 60
KEY_F3 = 61
KEY_F4 = 62
KEY_F5 = 63
KEY_F6 = 64
KEY_F7 = 65
KEY_F8 = 66
KEY_F9 = 67
KEY_F10 = 68
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_HOME = 102
KEY_UP = 103
KEY_PAGEUP = 104
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_END = 107
KEY_DOWN = 108
KEY_PAGEDOWN = 109
KEY_INSERT = 110
KEY_DELETE = 111
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_COMPOSE = 127
KEY_F11 = 87
KEY_F12 = 88
KEY_FN = 0x1D0  # 464


#: Modifier key codes mapped to the flag they contribute.
MODIFIER_KEY_FLAGS: dict[int, ModifierFlags] = {
    KEY_FN: ModifierFlags.FUNCTION,
    KEY_LEFTMETA: ModifierFlags.SUPER,
    KEY_RIGHTMETA: ModifierFlags.SUPER,
    KEY_LEFTALT: ModifierFlags.ALT,
    KEY_RIGHTALT: ModifierFlags.ALT,
    KEY_LEFTCTRL: ModifierFlags.CONTROL,
    KEY_RIGHTCTRL: ModifierFlags.CONTROL,
    KEY_LEFTSHIFT: ModifierFlags.SHIFT,
    KEY_RIGHTSHIFT: ModifierFlags.SHIFT,
}

#: Stable display order for a modifier-only chord, mirroring the macOS ordering
#: (fn, then super, alt, control, shift; left before right).
MODIFIER_SORT_PRIORITY: dict[int, int] = {
    KEY_FN: 0,
    KEY_LEFTMETA: 1,
    KEY_RIGHTMETA: 2,
    KEY_LEFTALT: 3,
    KEY_RIGHTALT: 4,
    KEY_LEFTCTRL: 5,
    KEY_RIGHTCTRL: 6,
    KEY_LEFTSHIFT: 7,
    KEY_RIGHTSHIFT: 8,
}

#: Names for keys whose label never depends on the active keyboard layout.
FIXED_KEY_NAMES: dict[int, str] = {
    KEY_ENTER: "Return",
    KEY_TAB: "Tab",
    KEY_SPACE: "Space",
    KEY_BACKSPACE: "Backspace",
    KEY_DELETE: "Delete",
    KEY_ESC: "Escape",
    KEY_LEFTMETA: "Left Super",
    KEY_RIGHTMETA: "Right Super",
    KEY_LEFTALT: "Left Alt",
    KEY_RIGHTALT: "Right Alt",
    KEY_LEFTCTRL: "Left Ctrl",
    KEY_RIGHTCTRL: "Right Ctrl",
    KEY_LEFTSHIFT: "Left Shift",
    KEY_RIGHTSHIFT: "Right Shift",
    KEY_CAPSLOCK: "Caps Lock",
    KEY_FN: "fn",
    KEY_LEFT: "Left",
    KEY_RIGHT: "Right",
    KEY_DOWN: "Down",
    KEY_UP: "Up",
    KEY_HOME: "Home",
    KEY_END: "End",
    KEY_PAGEUP: "Page Up",
    KEY_PAGEDOWN: "Page Down",
    KEY_INSERT: "Insert",
}

#: US QWERTY names used when live xkb layout data is unavailable.
QWERTY_FALLBACK: dict[int, str] = {
    KEY_A: "A", KEY_B: "B", KEY_C: "C", KEY_D: "D", KEY_E: "E", KEY_F: "F",
    KEY_G: "G", KEY_H: "H", KEY_I: "I", KEY_J: "J", KEY_K: "K", KEY_L: "L",
    KEY_M: "M", KEY_N: "N", KEY_O: "O", KEY_P: "P", KEY_Q: "Q", KEY_R: "R",
    KEY_S: "S", KEY_T: "T", KEY_U: "U", KEY_V: "V", KEY_W: "W", KEY_X: "X",
    KEY_Y: "Y", KEY_Z: "Z",
    KEY_1: "1", KEY_2: "2", KEY_3: "3", KEY_4: "4", KEY_5: "5",
    KEY_6: "6", KEY_7: "7", KEY_8: "8", KEY_9: "9", KEY_0: "0",
    KEY_MINUS: "-", KEY_EQUAL: "=", KEY_LEFTBRACE: "[", KEY_RIGHTBRACE: "]",
    KEY_SEMICOLON: ";", KEY_APOSTROPHE: "'", KEY_GRAVE: "`",
    KEY_BACKSLASH: "\\", KEY_COMMA: ",", KEY_DOT: ".", KEY_SLASH: "/",
    KEY_F1: "F1", KEY_F2: "F2", KEY_F3: "F3", KEY_F4: "F4", KEY_F5: "F5",
    KEY_F6: "F6", KEY_F7: "F7", KEY_F8: "F8", KEY_F9: "F9", KEY_F10: "F10",
    KEY_F11: "F11", KEY_F12: "F12",
}


def modifier_flag_for_key_code(key_code: int) -> ModifierFlags | None:
    """The flag a modifier key contributes, or None for a regular key."""
    return MODIFIER_KEY_FLAGS.get(key_code)
