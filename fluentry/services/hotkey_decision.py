"""Modifier-only shortcut press state machine.

A direct port of `ModifierOnlyShortcutFlagsDecision` from the macOS build. It
is pure and side-effect free so the start/finish rules can be tested without a
real input grab.

The two branches mirror the original:

* **Branch 1** — the shortcut carries explicit modifier key codes (a captured
  "Left Alt"), so arming and finishing are side-specific.
* **Branch 2** — the shortcut is flag-only, so arming is side-agnostic (a
  stored Left Alt still arms on Right Alt) while finishing still requires the
  stored key code.

Both branches only arm on the *first* press (`active_modifier_only_type is
None`). Without that precondition, releasing an unrelated Shift — or pressing
the sibling modifier — shrinks the pressed set back to the expected set,
re-enters `start`, and erases the "another key was pressed" flag, so the later
release reads as a clean tap and falsely starts recording (issue #688).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from ..models.hotkey import (
    RELEVANT_MODIFIER_MASK,
    HotkeyShortcut,
    normalized_modifier_key_codes,
)
from ..models.keycodes import ModifierFlags, modifier_flag_for_key_code


class HoldModeType(Enum):
    TRANSCRIPTION = "transcription"
    PROMPT_MODE = "promptMode"
    COMMAND_MODE = "commandMode"
    REWRITE_MODE = "rewriteMode"
    PROMPT_ASSIGNMENT = "promptAssignment"


class OutcomeKind(Enum):
    IGNORE = "ignore"
    START = "start"
    FINISH = "finish"


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    was_clean_press: bool = False

    @staticmethod
    def ignore() -> "Outcome":
        return Outcome(OutcomeKind.IGNORE)

    @staticmethod
    def start() -> "Outcome":
        return Outcome(OutcomeKind.START)

    @staticmethod
    def finish(was_clean_press: bool) -> "Outcome":
        return Outcome(OutcomeKind.FINISH, was_clean_press)


@dataclass(frozen=True)
class ModifierOnlyTrackingState:
    pressed_modifier_key_codes: frozenset[int] = frozenset()
    active_modifier_only_type: HoldModeType | None = None
    active_modifier_only_shortcut: HotkeyShortcut | None = None
    other_key_pressed_during_modifier: bool = False
    is_mode_key_pressed: bool = False


@dataclass(frozen=True)
class ModifierOnlyDecision:
    outcome: Outcome
    mark_interrupted: bool
    active_modifier_only_type: HoldModeType | None
    active_modifier_only_shortcut: HotkeyShortcut | None
    other_key_pressed_during_modifier: bool


def evaluate_modifier_only_flags(
    shortcut: HotkeyShortcut,
    hold_mode_type: HoldModeType,
    is_enabled: bool,
    key_code: int,
    modifiers: ModifierFlags,
    state: ModifierOnlyTrackingState,
) -> ModifierOnlyDecision:
    pressed_modifier_key_codes: Iterable[int] = state.pressed_modifier_key_codes
    active_type = state.active_modifier_only_type
    active_shortcut = state.active_modifier_only_shortcut
    other_key_pressed = state.other_key_pressed_during_modifier
    is_mode_key_pressed = state.is_mode_key_pressed

    def unchanged(mark_interrupted: bool = False) -> ModifierOnlyDecision:
        return ModifierOnlyDecision(
            outcome=Outcome.ignore(),
            mark_interrupted=mark_interrupted,
            active_modifier_only_type=active_type,
            active_modifier_only_shortcut=active_shortcut,
            other_key_pressed_during_modifier=True if mark_interrupted else other_key_pressed,
        )

    if not is_enabled or not shortcut.is_modifier_only_shortcut:
        return unchanged()

    relevant_modifiers = ModifierFlags(modifiers).intersection(RELEVANT_MODIFIER_MASK)
    expected_modifier_key_codes = shortcut.normalized_modifier_key_codes

    if expected_modifier_key_codes:
        pressed_key_codes = normalized_modifier_key_codes(pressed_modifier_key_codes)

        if (
            active_type is None
            and pressed_key_codes == expected_modifier_key_codes
            and key_code in expected_modifier_key_codes
        ):
            return ModifierOnlyDecision(
                outcome=Outcome.start(),
                mark_interrupted=False,
                active_modifier_only_type=hold_mode_type,
                active_modifier_only_shortcut=shortcut,
                other_key_pressed_during_modifier=False,
            )

        is_active_press = active_type == hold_mode_type and active_shortcut == shortcut
        is_legacy_mode_press = active_shortcut is None and is_mode_key_pressed
        mark_interrupted = False
        if is_active_press or is_legacy_mode_press:
            extra = [code for code in pressed_key_codes if code not in expected_modifier_key_codes]
            mark_interrupted = bool(extra)

        if not (
            (is_active_press or is_legacy_mode_press)
            and key_code in expected_modifier_key_codes
            and key_code not in pressed_key_codes
        ):
            return unchanged(mark_interrupted)

        was_clean_press = not (mark_interrupted or other_key_pressed)
        return ModifierOnlyDecision(
            outcome=Outcome.finish(was_clean_press),
            mark_interrupted=mark_interrupted,
            active_modifier_only_type=None,
            active_modifier_only_shortcut=None,
            other_key_pressed_during_modifier=False,
        )

    expected_pressed_modifiers = shortcut.expected_modifier_flags
    trigger_flag = shortcut.modifier_trigger_flag
    if expected_pressed_modifiers is None or trigger_flag is None:
        return unchanged()

    changed_modifier_flag = modifier_flag_for_key_code(key_code)
    if (
        active_type is None
        and relevant_modifiers == expected_pressed_modifiers
        and changed_modifier_flag is not None
        and expected_pressed_modifiers.contains(changed_modifier_flag)
    ):
        return ModifierOnlyDecision(
            outcome=Outcome.start(),
            mark_interrupted=False,
            active_modifier_only_type=hold_mode_type,
            active_modifier_only_shortcut=shortcut,
            other_key_pressed_during_modifier=False,
        )

    is_active_press = active_type == hold_mode_type and active_shortcut == shortcut
    is_legacy_mode_press = active_shortcut is None and is_mode_key_pressed
    mark_interrupted = False
    if is_active_press or is_legacy_mode_press:
        unexpected = relevant_modifiers.subtracting(expected_pressed_modifiers)
        mark_interrupted = not unexpected.is_empty

    if not (
        (is_active_press or is_legacy_mode_press)
        and key_code == shortcut.key_code
        and not relevant_modifiers.contains(trigger_flag)
    ):
        return unchanged(mark_interrupted)

    was_clean_press = not (mark_interrupted or other_key_pressed)
    return ModifierOnlyDecision(
        outcome=Outcome.finish(was_clean_press),
        mark_interrupted=mark_interrupted,
        active_modifier_only_type=None,
        active_modifier_only_shortcut=None,
        other_key_pressed_during_modifier=False,
    )


def should_force_stop_interrupted_primary_press(activation_mode: str) -> bool:
    """Hold and automatic presses stop on interruption; toggle keeps recording."""
    return activation_mode != "toggle"


def active_shortcut_summary(
    *,
    primary: list[HotkeyShortcut],
    prompt_assignments: list[tuple[str, HotkeyShortcut]],
    secondary_prompt_mode: HotkeyShortcut,
    secondary_prompt_mode_enabled: bool,
    command: HotkeyShortcut | None,
    command_enabled: bool,
    edit: HotkeyShortcut,
    edit_enabled: bool,
    cancel: HotkeyShortcut,
    paste_last: HotkeyShortcut | None,
    paste_last_enabled: bool,
    mode: str,
) -> str:
    """One line listing every shortcut the manager acts on and where it came from."""

    def describe(shortcut: HotkeyShortcut | None) -> str:
        if shortcut is None:
            return "none"
        flags = int(shortcut.relevant_modifier_flags)
        if shortcut.is_mouse_shortcut:
            button = shortcut.mouse_button if shortcut.mouse_button is not None else -1
            return f"{shortcut.display_string} [button={button} flags={flags}]"
        return f"{shortcut.display_string} [keyCode={shortcut.key_code} flags={flags}]"

    parts = [f"mode={mode}"]
    parts += [f"primary[{index}]={describe(item)}" for index, item in enumerate(primary)]
    parts += [f"prompt[{key}]={describe(item)}" for key, item in prompt_assignments]
    parts.append(f"secondaryPromptMode={describe(secondary_prompt_mode)} enabled={_bool(secondary_prompt_mode_enabled)}")
    parts.append(f"command={describe(command)} enabled={_bool(command_enabled)}")
    parts.append(f"edit={describe(edit)} enabled={_bool(edit_enabled)}")
    parts.append(f"cancel={describe(cancel)}")
    parts.append(f"pasteLast={describe(paste_last)} enabled={_bool(paste_last_enabled)}")
    return " | ".join(parts)


def _bool(value: bool) -> str:
    return "true" if value else "false"
