"""Hotkey shortcut value type.

A direct port of the macOS `HotkeyShortcut`, retargeted at evdev key codes.
Every behaviour the original guaranteed is preserved:

* a modifier-only shortcut (tap Right Alt) is distinct from a chord,
* mouse shortcuts never match keyboard events and vice versa,
* an unmodified left/right click can never be a shortcut,
* two shortcuts "conflict" when one press is a prefix of the other,
* the encoded form is backwards compatible with payloads that predate
  `kind` and `modifierKeyCodes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .keycodes import (
    FIXED_KEY_NAMES,
    MODIFIER_SORT_PRIORITY,
    QWERTY_FALLBACK,
    ModifierFlags,
    modifier_flag_for_key_code,
)

RELEVANT_MODIFIER_MASK = (
    ModifierFlags.FUNCTION
    | ModifierFlags.SUPER
    | ModifierFlags.ALT
    | ModifierFlags.CONTROL
    | ModifierFlags.SHIFT
)

#: Hook used to resolve a key code's label from the live keyboard layout.
#: Returns None when layout data is unavailable, which falls back to QWERTY.
_layout_resolver: Callable[[int], str | None] | None = None


def set_layout_resolver(resolver: Callable[[int], str | None] | None) -> None:
    """Install (or clear) the live keyboard-layout label resolver."""
    global _layout_resolver
    _layout_resolver = resolver


def character_for_key_code(key_code: int) -> str | None:
    """Label for a key code from the active layout, uppercased when unambiguous."""
    if _layout_resolver is None:
        return None
    try:
        raw = _layout_resolver(key_code)
    except Exception:
        return None
    if not raw:
        return None
    if any(ord(ch) < 0x20 for ch in raw):
        return None
    upper = raw.upper()
    return upper if len(upper) == len(raw) else raw


def normalized_modifier_key_codes(modifier_key_codes: Iterable[int]) -> list[int]:
    """Deduplicate, drop non-modifiers, and sort into stable display order."""
    unique = set(modifier_key_codes)
    ranked = [(code, MODIFIER_SORT_PRIORITY[code]) for code in unique if code in MODIFIER_SORT_PRIORITY]
    ranked.sort(key=lambda pair: pair[1])
    return [code for code, _ in ranked]


def key_code_to_string(key_code: int) -> str | None:
    fixed = FIXED_KEY_NAMES.get(key_code)
    if fixed is not None:
        return fixed
    return character_for_key_code(key_code) or QWERTY_FALLBACK.get(key_code)


def mouse_button_to_string(button: int) -> str:
    if button == 0:
        return "Left Click"
    if button == 1:
        return "Right Click"
    if button == 2:
        return "Middle Click"
    return f"Mouse {button + 1}"


def _modifier_display_parts(flags: ModifierFlags) -> list[str]:
    parts: list[str] = []
    if flags.contains(ModifierFlags.FUNCTION):
        parts.append("fn")
    if flags.contains(ModifierFlags.SUPER):
        parts.append("Super")
    if flags.contains(ModifierFlags.ALT):
        parts.append("Alt")
    if flags.contains(ModifierFlags.CONTROL):
        parts.append("Ctrl")
    if flags.contains(ModifierFlags.SHIFT):
        parts.append("Shift")
    return parts


KEYBOARD = "keyboard"
MOUSE = "mouse"


@dataclass(frozen=True)
class HotkeyShortcut:
    kind: str = KEYBOARD
    key_code: int = 0
    modifier_flags: ModifierFlags = ModifierFlags.NONE
    modifier_key_codes: tuple[int, ...] = ()
    mouse_button: int | None = None

    # --- constructors -----------------------------------------------------

    @staticmethod
    def keyboard(
        key_code: int,
        modifier_flags: ModifierFlags = ModifierFlags.NONE,
        modifier_key_codes: Iterable[int] = (),
    ) -> "HotkeyShortcut":
        normalized = normalized_modifier_key_codes(modifier_key_codes)
        if normalized:
            trigger = normalized[0]
            combined = ModifierFlags.NONE
            for code in normalized:
                flag = modifier_flag_for_key_code(code)
                if flag is not None:
                    combined = combined.union(flag)
            trigger_flag = modifier_flag_for_key_code(trigger)
            if trigger_flag is not None:
                flags = combined.subtracting(trigger_flag)
            else:
                flags = ModifierFlags(modifier_flags).intersection(RELEVANT_MODIFIER_MASK)
            return HotkeyShortcut(
                kind=KEYBOARD,
                key_code=trigger,
                modifier_flags=flags,
                modifier_key_codes=tuple(normalized),
                mouse_button=None,
            )
        return HotkeyShortcut(
            kind=KEYBOARD,
            key_code=key_code,
            modifier_flags=ModifierFlags(modifier_flags),
            modifier_key_codes=(),
            mouse_button=None,
        )

    @staticmethod
    def mouse(mouse_button: int, modifier_flags: ModifierFlags = ModifierFlags.NONE) -> "HotkeyShortcut":
        return HotkeyShortcut(
            kind=MOUSE,
            key_code=0,
            modifier_flags=ModifierFlags(modifier_flags).intersection(RELEVANT_MODIFIER_MASK),
            modifier_key_codes=(),
            mouse_button=mouse_button,
        )

    # --- derived properties ----------------------------------------------

    @property
    def is_mouse_shortcut(self) -> bool:
        return self.kind == MOUSE

    @property
    def relevant_modifier_flags(self) -> ModifierFlags:
        return ModifierFlags(self.modifier_flags).intersection(RELEVANT_MODIFIER_MASK)

    @property
    def is_unmodified_left_or_right_click(self) -> bool:
        if not self.is_mouse_shortcut or self.mouse_button is None:
            return False
        return self.mouse_button in (0, 1) and self.relevant_modifier_flags.is_empty

    @property
    def normalized_modifier_key_codes(self) -> list[int]:
        if self.is_mouse_shortcut:
            return []
        normalized = normalized_modifier_key_codes(self.modifier_key_codes)
        if normalized:
            return normalized
        if self.modifier_trigger_flag is not None and self.relevant_modifier_flags.is_empty:
            return [self.key_code]
        return []

    @property
    def modifier_trigger_flag(self) -> ModifierFlags | None:
        if self.is_mouse_shortcut:
            return None
        return modifier_flag_for_key_code(self.key_code)

    @property
    def is_modifier_only_shortcut(self) -> bool:
        return self.modifier_trigger_flag is not None

    @property
    def expected_modifier_flags(self) -> ModifierFlags | None:
        trigger = self.modifier_trigger_flag
        if trigger is None:
            return None
        return self.relevant_modifier_flags.union(trigger)

    @property
    def display_string(self) -> str:
        if self.is_mouse_shortcut and self.mouse_button is not None:
            parts = _modifier_display_parts(self.relevant_modifier_flags)
            parts.append(mouse_button_to_string(self.mouse_button))
            return " + ".join(parts)

        modifier_codes = self.normalized_modifier_key_codes
        modifier_parts = [name for name in (key_code_to_string(c) for c in modifier_codes) if name]
        if modifier_parts:
            return " + ".join(modifier_parts)

        parts = _modifier_display_parts(ModifierFlags(self.modifier_flags))
        parts.append(key_code_to_string(self.key_code) or "?")
        if ModifierFlags(self.modifier_flags).is_empty:
            return parts[-1] if parts else "Unknown"
        return " + ".join(parts)

    # --- matching ---------------------------------------------------------

    def matches(self, key_code: int, modifiers: ModifierFlags) -> bool:
        if self.is_mouse_shortcut:
            return False
        return (
            key_code == self.key_code
            and ModifierFlags(modifiers).intersection(RELEVANT_MODIFIER_MASK) == self.relevant_modifier_flags
        )

    def matches_mouse(self, button: int, modifiers: ModifierFlags) -> bool:
        if not self.is_mouse_shortcut or self.mouse_button is None:
            return False
        if self.is_unmodified_left_or_right_click:
            return False
        return (
            self.mouse_button == button
            and ModifierFlags(modifiers).intersection(RELEVANT_MODIFIER_MASK) == self.relevant_modifier_flags
        )

    def conflicts_with(self, other: "HotkeyShortcut") -> bool:
        """True when presses can race because one begins as the other's prefix."""
        if self.is_modifier_only_shortcut and other.is_modifier_only_shortcut:
            lhs = set(self.normalized_modifier_key_codes)
            rhs = set(other.normalized_modifier_key_codes)
            if not lhs or not rhs:
                return False
            return lhs.issubset(rhs) or rhs.issubset(lhs)
        if self.is_mouse_shortcut and other.is_modifier_only_shortcut:
            return self._mouse_modifiers_overlap(other)
        if other.is_mouse_shortcut and self.is_modifier_only_shortcut:
            return other._mouse_modifiers_overlap(self)
        return False

    def _mouse_modifiers_overlap(self, modifier_only_shortcut: "HotkeyShortcut") -> bool:
        if not self.is_mouse_shortcut:
            return False
        expected = modifier_only_shortcut.expected_modifier_flags
        if expected is None or expected.is_empty:
            return False
        return expected.is_subset(self.relevant_modifier_flags)

    # --- equality ---------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HotkeyShortcut):
            return NotImplemented
        if self.kind != other.kind:
            return False
        if self.kind == MOUSE:
            return (
                self.mouse_button == other.mouse_button
                and self.relevant_modifier_flags == other.relevant_modifier_flags
            )
        lhs = self.normalized_modifier_key_codes
        rhs = other.normalized_modifier_key_codes
        if lhs and rhs:
            return lhs == rhs
        return self.key_code == other.key_code and self.relevant_modifier_flags == other.relevant_modifier_flags

    def __hash__(self) -> int:
        if self.kind == MOUSE:
            return hash((MOUSE, self.mouse_button, int(self.relevant_modifier_flags)))
        normalized = tuple(self.normalized_modifier_key_codes)
        if normalized:
            return hash((KEYBOARD, normalized))
        return hash((KEYBOARD, self.key_code, int(self.relevant_modifier_flags)))

    # --- coding -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "modifierFlagsRawValue": int(self.modifier_flags),
        }
        if self.kind == KEYBOARD:
            payload["keyCode"] = self.key_code
            normalized = self.normalized_modifier_key_codes
            if normalized:
                payload["modifierKeyCodes"] = normalized
        else:
            if self.mouse_button is None:
                raise ValueError("Mouse shortcut is missing a mouse button")
            payload["mouseButton"] = self.mouse_button
        return payload

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "HotkeyShortcut":
        kind = payload.get("kind") or KEYBOARD
        raw = int(payload.get("modifierFlagsRawValue") or 0)
        flags = ModifierFlags(raw)
        if kind == MOUSE:
            button = payload["mouseButton"]
            return HotkeyShortcut.mouse(int(button), flags)
        key_code = int(payload["keyCode"])
        modifier_key_codes = payload.get("modifierKeyCodes") or []
        return HotkeyShortcut.keyboard(key_code, flags, [int(c) for c in modifier_key_codes])
