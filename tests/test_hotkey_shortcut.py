"""Port of HotkeyShortcutTests — shortcut value semantics and masks."""

import json

import pytest

from fluentry.models.hotkey import HotkeyShortcut, set_layout_resolver
from fluentry.models.keycodes import (
    KEY_A,
    KEY_ENTER,
    KEY_ESC,
    KEY_LEFTALT,
    KEY_LEFTCTRL,
    KEY_LEFTMETA,
    KEY_LEFTSHIFT,
    KEY_R,
    KEY_RIGHTALT,
    KEY_RIGHTSHIFT,
    ModifierFlags,
)
from fluentry.services.hotkey_decision import (
    HoldModeType,
    ModifierOnlyTrackingState,
    OutcomeKind,
    active_shortcut_summary,
    evaluate_modifier_only_flags,
    should_force_stop_interrupted_primary_press,
)
from fluentry.services.hotkey_events import (
    InputEventType,
    keyboard_event_mask,
    mask_contains,
    mouse_observer_event_mask,
    mouse_shortcut_event_mask,
    session_is_locked,
)


class ModifierOnlyFlagsReplay:
    """Mirror of the Swift test double driving the decision state machine."""

    def __init__(self, shortcut: HotkeyShortcut) -> None:
        self.shortcut = shortcut
        self.pressed_modifier_key_codes: frozenset[int] = frozenset()
        self.active_modifier_only_type = None
        self.active_modifier_only_shortcut = None
        self.other_key_pressed_during_modifier = False
        self.clean_finish_count = 0

    def flags_changed(self, key_code: int, modifiers: ModifierFlags, next_pressed) -> None:
        self.pressed_modifier_key_codes = frozenset(next_pressed)
        decision = evaluate_modifier_only_flags(
            shortcut=self.shortcut,
            hold_mode_type=HoldModeType.TRANSCRIPTION,
            is_enabled=True,
            key_code=key_code,
            modifiers=modifiers,
            state=ModifierOnlyTrackingState(
                pressed_modifier_key_codes=self.pressed_modifier_key_codes,
                active_modifier_only_type=self.active_modifier_only_type,
                active_modifier_only_shortcut=self.active_modifier_only_shortcut,
                other_key_pressed_during_modifier=self.other_key_pressed_during_modifier,
                is_mode_key_pressed=False,
            ),
        )
        self.active_modifier_only_type = decision.active_modifier_only_type
        self.active_modifier_only_shortcut = decision.active_modifier_only_shortcut
        self.other_key_pressed_during_modifier = decision.other_key_pressed_during_modifier
        if decision.outcome.kind is OutcomeKind.FINISH and decision.outcome.was_clean_press:
            self.clean_finish_count += 1

    def key_down(self) -> None:
        if self.active_modifier_only_type is not None:
            self.other_key_pressed_during_modifier = True

    def mouse_down(self) -> None:
        self.key_down()


# --- summary ---------------------------------------------------------------


def test_active_shortcut_summary_lists_every_source_with_key_codes():
    summary = active_shortcut_summary(
        primary=[HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE, [KEY_RIGHTALT])],
        prompt_assignments=[("__default__", HotkeyShortcut.keyboard(KEY_LEFTMETA, ModifierFlags.NONE, [KEY_LEFTMETA]))],
        secondary_prompt_mode=HotkeyShortcut.keyboard(KEY_RIGHTSHIFT, ModifierFlags.NONE),
        secondary_prompt_mode_enabled=False,
        command=None,
        command_enabled=False,
        edit=HotkeyShortcut.keyboard(KEY_R, ModifierFlags.ALT),
        edit_enabled=True,
        cancel=HotkeyShortcut.keyboard(KEY_ESC, ModifierFlags.NONE),
        paste_last=HotkeyShortcut.mouse(0, ModifierFlags.SUPER),
        paste_last_enabled=True,
        mode="automatic",
    )

    assert summary.startswith("mode=automatic")
    assert f"primary[0]=Right Alt [keyCode={KEY_RIGHTALT}" in summary
    assert f"prompt[__default__]=Left Super [keyCode={KEY_LEFTMETA}" in summary
    assert f"secondaryPromptMode=Right Shift [keyCode={KEY_RIGHTSHIFT} flags=0] enabled=false" in summary
    assert "command=none enabled=false" in summary
    assert f"edit=Alt + R [keyCode={KEY_R}" in summary
    assert f"cancel=Escape [keyCode={KEY_ESC}" in summary
    assert "pasteLast=Super + Left Click [button=0" in summary


# --- event masks -----------------------------------------------------------


def test_keyboard_event_mask_excludes_mouse_events():
    mask = keyboard_event_mask()
    for event in (InputEventType.KEY_DOWN, InputEventType.KEY_UP, InputEventType.FLAGS_CHANGED):
        assert mask_contains(mask, event), f"keyboard mask must include {event}"
    for event in (
        InputEventType.LEFT_MOUSE_DOWN,
        InputEventType.LEFT_MOUSE_UP,
        InputEventType.RIGHT_MOUSE_DOWN,
        InputEventType.RIGHT_MOUSE_UP,
        InputEventType.OTHER_MOUSE_DOWN,
        InputEventType.OTHER_MOUSE_UP,
    ):
        assert not mask_contains(mask, event), f"keyboard mask must not include {event}"


def test_mouse_observer_mask_covers_only_mouse_downs():
    mask = mouse_observer_event_mask()
    for event in (
        InputEventType.LEFT_MOUSE_DOWN,
        InputEventType.RIGHT_MOUSE_DOWN,
        InputEventType.OTHER_MOUSE_DOWN,
    ):
        assert mask_contains(mask, event)
    for event in (
        InputEventType.LEFT_MOUSE_UP,
        InputEventType.RIGHT_MOUSE_UP,
        InputEventType.OTHER_MOUSE_UP,
        InputEventType.KEY_DOWN,
        InputEventType.KEY_UP,
        InputEventType.FLAGS_CHANGED,
    ):
        assert not mask_contains(mask, event)


def test_mouse_shortcut_mask_matches_configured_buttons():
    assert mouse_shortcut_event_mask([]) == 0

    left_only = mouse_shortcut_event_mask([0])
    for event in (InputEventType.LEFT_MOUSE_DOWN, InputEventType.LEFT_MOUSE_UP):
        assert mask_contains(left_only, event)
    for event in (
        InputEventType.RIGHT_MOUSE_DOWN,
        InputEventType.RIGHT_MOUSE_UP,
        InputEventType.OTHER_MOUSE_DOWN,
        InputEventType.OTHER_MOUSE_UP,
        InputEventType.KEY_DOWN,
        InputEventType.FLAGS_CHANGED,
    ):
        assert not mask_contains(left_only, event)

    side_button = mouse_shortcut_event_mask([3])
    for event in (InputEventType.OTHER_MOUSE_DOWN, InputEventType.OTHER_MOUSE_UP):
        assert mask_contains(side_button, event)
    for event in (
        InputEventType.LEFT_MOUSE_DOWN,
        InputEventType.LEFT_MOUSE_UP,
        InputEventType.RIGHT_MOUSE_DOWN,
        InputEventType.RIGHT_MOUSE_UP,
    ):
        assert not mask_contains(side_button, event)


def test_hotkey_session_lock_detection():
    assert session_is_locked({"LockedHint": True})
    assert not session_is_locked({"LockedHint": False})
    assert not session_is_locked({})
    # The macOS key stays accepted so restored settings keep working.
    assert session_is_locked({"CGSSessionScreenIsLocked": True})


def test_interrupted_mouse_press_force_stops_hold_and_automatic_modes():
    assert should_force_stop_interrupted_primary_press("hold")
    assert should_force_stop_interrupted_primary_press("automatic")
    assert not should_force_stop_interrupted_primary_press("toggle")


# --- coding ----------------------------------------------------------------


def test_legacy_keyboard_shortcut_payload_defaults_to_keyboard_kind():
    payload = json.loads('{"keyCode":100,"modifierFlagsRawValue":0}')
    shortcut = HotkeyShortcut.from_dict(payload)

    assert shortcut.kind == "keyboard"
    assert not shortcut.is_mouse_shortcut
    assert shortcut.key_code == KEY_RIGHTALT
    assert shortcut.matches(KEY_RIGHTALT, ModifierFlags.NONE)


def test_keyboard_payload_ignores_stray_mouse_button_field():
    payload = json.loads(
        '{"kind":"keyboard","keyCode":30,"modifierFlagsRawValue":0,"mouseButton":3}'
    )
    shortcut = HotkeyShortcut.from_dict(payload)

    assert not shortcut.is_mouse_shortcut
    assert shortcut.display_string == "A"
    assert not shortcut.matches_mouse(3, ModifierFlags.NONE)


def test_mouse_shortcut_round_trips_and_matches_only_mouse_events():
    shortcut = HotkeyShortcut.mouse(3, ModifierFlags.ALT)
    decoded = HotkeyShortcut.from_dict(json.loads(json.dumps(shortcut.to_dict())))

    assert decoded.kind == "mouse"
    assert decoded.is_mouse_shortcut
    assert decoded.mouse_button == 3
    assert decoded.matches_mouse(3, ModifierFlags.ALT)
    assert not decoded.matches_mouse(3, ModifierFlags.NONE)
    assert not decoded.matches(0, ModifierFlags.ALT)


def test_unmodified_left_and_right_clicks_do_not_match_mouse_events():
    left_click = HotkeyShortcut.mouse(0, ModifierFlags.NONE)
    right_click = HotkeyShortcut.mouse(1, ModifierFlags.NONE)
    side_button = HotkeyShortcut.mouse(3, ModifierFlags.NONE)
    modified_left_click = HotkeyShortcut.mouse(0, ModifierFlags.CONTROL)

    assert left_click.is_unmodified_left_or_right_click
    assert right_click.is_unmodified_left_or_right_click
    assert not left_click.matches_mouse(0, ModifierFlags.NONE)
    assert not right_click.matches_mouse(1, ModifierFlags.NONE)
    assert side_button.matches_mouse(3, ModifierFlags.NONE)
    assert modified_left_click.matches_mouse(0, ModifierFlags.CONTROL)


def test_mouse_shortcut_display_includes_modifiers():
    shortcut = HotkeyShortcut.mouse(0, ModifierFlags.CONTROL | ModifierFlags.SHIFT)
    assert shortcut.display_string == "Ctrl + Shift + Left Click"


def test_mouse_shortcut_does_not_equal_keyboard_shortcut_with_placeholder_key_code():
    mouse_shortcut = HotkeyShortcut.mouse(3, ModifierFlags.NONE)
    keyboard_shortcut = HotkeyShortcut.keyboard(0, ModifierFlags.NONE)

    assert mouse_shortcut.display_string == "Mouse 4"
    assert mouse_shortcut != keyboard_shortcut


def test_mouse_shortcut_encoding_requires_a_button():
    broken = HotkeyShortcut(kind="mouse", key_code=0, modifier_flags=ModifierFlags.NONE)
    with pytest.raises(ValueError):
        broken.to_dict()


def test_modified_mouse_shortcut_conflicts_with_modifier_only_shortcut():
    alt_only = HotkeyShortcut.keyboard(KEY_RIGHTALT, ModifierFlags.NONE)
    modified_click = HotkeyShortcut.mouse(0, ModifierFlags.ALT)
    unmodified_side_button = HotkeyShortcut.mouse(3, ModifierFlags.NONE)

    assert modified_click.conflicts_with(alt_only)
    assert alt_only.conflicts_with(modified_click)
    assert not unmodified_side_button.conflicts_with(alt_only)


def test_layout_resolver_labels_keys_and_falls_back_to_qwerty():
    try:
        set_layout_resolver(lambda code: {KEY_A: "q"}.get(code))
        assert HotkeyShortcut.keyboard(KEY_A).display_string == "Q"
        # Unmapped codes still fall back to the QWERTY table.
        assert HotkeyShortcut.keyboard(KEY_R).display_string == "R"
    finally:
        set_layout_resolver(None)
    assert HotkeyShortcut.keyboard(KEY_A).display_string == "A"


# --- modifier-only press state machine -------------------------------------


def test_modifier_only_shortcut_ignores_tap_after_mouse_click():
    replay = ModifierOnlyFlagsReplay(
        HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    )

    replay.flags_changed(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    assert replay.active_modifier_only_type is HoldModeType.TRANSCRIPTION

    replay.mouse_down()
    replay.flags_changed(KEY_LEFTALT, ModifierFlags.NONE, [])

    assert replay.clean_finish_count == 0, "Alt+click must not read as an Alt tap"
    assert replay.active_modifier_only_type is None


def test_modifier_only_shortcut_does_not_fire_on_unrelated_shift_key_combo():
    replay = ModifierOnlyFlagsReplay(
        HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    )

    replay.flags_changed(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    assert replay.active_modifier_only_type is HoldModeType.TRANSCRIPTION
    assert replay.clean_finish_count == 0

    replay.flags_changed(KEY_LEFTSHIFT, ModifierFlags.ALT | ModifierFlags.SHIFT, [KEY_LEFTSHIFT, KEY_LEFTALT])
    assert replay.other_key_pressed_during_modifier

    replay.key_down()
    assert replay.other_key_pressed_during_modifier

    replay.flags_changed(KEY_LEFTSHIFT, ModifierFlags.ALT, [KEY_LEFTALT])
    replay.flags_changed(KEY_LEFTALT, ModifierFlags.NONE, [])

    assert replay.clean_finish_count == 0
    assert replay.active_modifier_only_type is None


def test_modifier_only_shortcut_fires_on_genuine_modifier_tap():
    replay = ModifierOnlyFlagsReplay(
        HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    )

    replay.flags_changed(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    assert replay.active_modifier_only_type is HoldModeType.TRANSCRIPTION

    replay.flags_changed(KEY_LEFTALT, ModifierFlags.NONE, [])

    assert replay.clean_finish_count == 1
    assert replay.active_modifier_only_type is None


def test_modifier_only_shortcut_ignores_shift_combo_from_idle():
    replay = ModifierOnlyFlagsReplay(
        HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    )

    replay.flags_changed(KEY_LEFTSHIFT, ModifierFlags.SHIFT, [KEY_LEFTSHIFT])
    replay.key_down()

    assert replay.active_modifier_only_type is None
    assert replay.clean_finish_count == 0


def test_branch2_modifier_only_shortcut_arms_on_sibling_and_ignores_shift_combo():
    shortcut = HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT)
    assert not shortcut.normalized_modifier_key_codes, "precondition: flag-only shortcut takes branch 2"

    sibling = ModifierOnlyFlagsReplay(shortcut)
    sibling.flags_changed(KEY_RIGHTALT, ModifierFlags.ALT, [KEY_RIGHTALT])
    assert sibling.active_modifier_only_type is HoldModeType.TRANSCRIPTION

    combo = ModifierOnlyFlagsReplay(shortcut)
    combo.flags_changed(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    combo.flags_changed(KEY_LEFTSHIFT, ModifierFlags.ALT | ModifierFlags.SHIFT, [KEY_LEFTSHIFT, KEY_LEFTALT])
    combo.key_down()
    combo.flags_changed(KEY_LEFTSHIFT, ModifierFlags.ALT, [KEY_LEFTALT])
    combo.flags_changed(KEY_LEFTALT, ModifierFlags.NONE, [])

    assert combo.clean_finish_count == 0


def test_branch2_modifier_only_shortcut_sibling_press_does_not_erase_interrupt():
    replay = ModifierOnlyFlagsReplay(HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT))
    assert not replay.shortcut.normalized_modifier_key_codes

    replay.flags_changed(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    assert replay.active_modifier_only_type is HoldModeType.TRANSCRIPTION
    replay.key_down()
    assert replay.other_key_pressed_during_modifier
    replay.flags_changed(KEY_RIGHTALT, ModifierFlags.ALT, [KEY_LEFTALT, KEY_RIGHTALT])

    assert replay.other_key_pressed_during_modifier
    assert replay.active_modifier_only_type is HoldModeType.TRANSCRIPTION

    replay.flags_changed(KEY_RIGHTALT, ModifierFlags.ALT, [KEY_LEFTALT])
    replay.flags_changed(KEY_LEFTALT, ModifierFlags.NONE, [])

    assert replay.clean_finish_count == 0


def test_disabled_shortcut_and_non_modifier_shortcut_are_ignored():
    state = ModifierOnlyTrackingState()
    decision = evaluate_modifier_only_flags(
        shortcut=HotkeyShortcut.keyboard(KEY_LEFTALT, ModifierFlags.ALT, [KEY_LEFTALT]),
        hold_mode_type=HoldModeType.TRANSCRIPTION,
        is_enabled=False,
        key_code=KEY_LEFTALT,
        modifiers=ModifierFlags.ALT,
        state=state,
    )
    assert decision.outcome.kind is OutcomeKind.IGNORE

    decision = evaluate_modifier_only_flags(
        shortcut=HotkeyShortcut.keyboard(KEY_ENTER, ModifierFlags.CONTROL),
        hold_mode_type=HoldModeType.TRANSCRIPTION,
        is_enabled=True,
        key_code=KEY_ENTER,
        modifiers=ModifierFlags.CONTROL,
        state=state,
    )
    assert decision.outcome.kind is OutcomeKind.IGNORE


def test_modifier_only_chord_normalizes_and_displays_in_stable_order():
    chord = HotkeyShortcut.keyboard(
        0, ModifierFlags.NONE, [KEY_LEFTSHIFT, KEY_LEFTCTRL, KEY_LEFTMETA]
    )
    assert chord.normalized_modifier_key_codes == [KEY_LEFTMETA, KEY_LEFTCTRL, KEY_LEFTSHIFT]
    assert chord.display_string == "Left Super + Left Ctrl + Left Shift"
    # The trigger is the first code and its own flag is excluded from the chord flags.
    assert chord.key_code == KEY_LEFTMETA
    assert chord.modifier_flags == ModifierFlags.CONTROL | ModifierFlags.SHIFT


def test_modifier_only_chord_conflicts_with_its_own_prefix():
    chord = HotkeyShortcut.keyboard(0, ModifierFlags.NONE, [KEY_LEFTCTRL, KEY_LEFTSHIFT])
    prefix = HotkeyShortcut.keyboard(KEY_LEFTCTRL, ModifierFlags.NONE, [KEY_LEFTCTRL])
    unrelated = HotkeyShortcut.keyboard(KEY_LEFTMETA, ModifierFlags.NONE, [KEY_LEFTMETA])

    assert chord.conflicts_with(prefix)
    assert prefix.conflicts_with(chord)
    assert not chord.conflicts_with(unrelated)
