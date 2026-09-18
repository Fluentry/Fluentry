"""Wire input events to dictation actions.

The orchestration half of `GlobalHotkeyManager`: it owns the shortcut set,
feeds modifier transitions through the pure decision machine, applies the
activation mode, and calls back into the app.

Everything stateful lives here; everything decidable lives in
`hotkey_decision` and `hotkey_activation`, which is why the interesting rules
are testable without an input grab.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..models.hotkey import HotkeyShortcut
from ..models.keycodes import ModifierFlags
from ..persistence.settings_store import HotkeyActivationMode
from ..platform.hotkey_listener import InputEvent, SyntheticHotkeyBackend
from .hotkey_activation import ActivationAction, HotkeyActivationState
from .hotkey_decision import (
    HoldModeType,
    ModifierOnlyTrackingState,
    OutcomeKind,
    active_shortcut_summary,
    evaluate_modifier_only_flags,
)
from .hotkey_events import InputEventType, mouse_shortcut_event_mask

MOUSE_DOWN_EVENTS = {
    InputEventType.LEFT_MOUSE_DOWN,
    InputEventType.RIGHT_MOUSE_DOWN,
    InputEventType.OTHER_MOUSE_DOWN,
}
MOUSE_UP_EVENTS = {
    InputEventType.LEFT_MOUSE_UP,
    InputEventType.RIGHT_MOUSE_UP,
    InputEventType.OTHER_MOUSE_UP,
}


@dataclass
class HotkeyBinding:
    """One shortcut and the mode it drives."""

    shortcut: HotkeyShortcut
    hold_mode_type: HoldModeType
    enabled: bool = True
    activation: HotkeyActivationState = field(default_factory=HotkeyActivationState)
    #: Tracking state for a modifier-only press (tap Right Alt, etc.).
    tracking: ModifierOnlyTrackingState = field(default_factory=ModifierOnlyTrackingState)


class GlobalHotkeyManager:
    def __init__(
        self,
        backend=None,
        mode: str = HotkeyActivationMode.TOGGLE,
        is_recording: Callable[[], bool] | None = None,
        on_start: Callable[[HoldModeType], None] | None = None,
        on_stop: Callable[[HoldModeType], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_paste_last: Callable[[], None] | None = None,
        session_is_locked: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend if backend is not None else SyntheticHotkeyBackend()
        self.mode = mode
        self._is_recording = is_recording or (lambda: False)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self._on_paste_last = on_paste_last
        self._session_is_locked = session_is_locked or (lambda: False)
        self._clock = clock
        self._lock = threading.RLock()

        self.primary_shortcuts: list[HotkeyShortcut] = []
        self.bindings: list[HotkeyBinding] = []
        self.cancel_shortcut: HotkeyShortcut | None = None
        self.paste_last_shortcut: HotkeyShortcut | None = None
        self.paste_last_enabled = False
        self._started = False

    # --- configuration ----------------------------------------------------

    def configure(
        self,
        primary_shortcuts: list[HotkeyShortcut],
        mode: str | None = None,
        cancel_shortcut: HotkeyShortcut | None = None,
        paste_last_shortcut: HotkeyShortcut | None = None,
        paste_last_enabled: bool = False,
        extra_bindings: list[HotkeyBinding] | None = None,
    ) -> None:
        with self._lock:
            if mode is not None:
                self.mode = mode
            self.primary_shortcuts = list(primary_shortcuts)
            self.cancel_shortcut = cancel_shortcut
            self.paste_last_shortcut = paste_last_shortcut
            self.paste_last_enabled = paste_last_enabled
            self.bindings = [
                HotkeyBinding(
                    shortcut=shortcut,
                    hold_mode_type=HoldModeType.TRANSCRIPTION,
                    activation=HotkeyActivationState(mode=self.mode),
                )
                for shortcut in self.primary_shortcuts
            ]
            for binding in extra_bindings or []:
                binding.activation.mode = self.mode
                self.bindings.append(binding)

    @property
    def configured_mouse_buttons(self) -> set[int]:
        buttons = set()
        for binding in self.bindings:
            if binding.shortcut.is_mouse_shortcut and binding.shortcut.mouse_button is not None:
                buttons.add(binding.shortcut.mouse_button)
        if self.paste_last_enabled and self.paste_last_shortcut is not None:
            if self.paste_last_shortcut.mouse_button is not None:
                buttons.add(self.paste_last_shortcut.mouse_button)
        return buttons

    @property
    def mouse_event_mask(self) -> int:
        return mouse_shortcut_event_mask(self.configured_mouse_buttons)

    def summary(self, prompt_assignments=None) -> str:
        from ..models.keycodes import KEY_ESC

        return active_shortcut_summary(
            primary=self.primary_shortcuts,
            prompt_assignments=prompt_assignments or [],
            secondary_prompt_mode=HotkeyShortcut.keyboard(KEY_ESC),
            secondary_prompt_mode_enabled=False,
            command=None,
            command_enabled=False,
            edit=HotkeyShortcut.keyboard(KEY_ESC),
            edit_enabled=False,
            cancel=self.cancel_shortcut or HotkeyShortcut.keyboard(KEY_ESC),
            paste_last=self.paste_last_shortcut,
            paste_last_enabled=self.paste_last_enabled,
            mode=self.mode,
        )

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self.backend.start(self.handle_event)

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        self.backend.stop()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            for binding in self.bindings:
                binding.activation.reset()
                binding.tracking = ModifierOnlyTrackingState()

    # --- event handling ---------------------------------------------------

    def handle_event(self, event: InputEvent) -> None:
        # A locked screen must never start a recording the user cannot see.
        if self._session_is_locked():
            self.reset()
            return
        with self._lock:
            if event.type is InputEventType.FLAGS_CHANGED:
                self._handle_flags_changed(event)
            elif event.type is InputEventType.KEY_DOWN:
                self._handle_key_down(event)
            elif event.type is InputEventType.KEY_UP:
                self._handle_key_up(event)
            elif event.type in MOUSE_DOWN_EVENTS:
                self._handle_mouse_down(event)
            elif event.type in MOUSE_UP_EVENTS:
                self._handle_mouse_up(event)

    def _handle_flags_changed(self, event: InputEvent) -> None:
        for binding in self.bindings:
            if not binding.shortcut.is_modifier_only_shortcut:
                continue
            decision = evaluate_modifier_only_flags(
                shortcut=binding.shortcut,
                hold_mode_type=binding.hold_mode_type,
                is_enabled=binding.enabled,
                key_code=event.key_code,
                modifiers=event.modifiers,
                state=ModifierOnlyTrackingState(
                    pressed_modifier_key_codes=event.pressed_modifier_key_codes,
                    active_modifier_only_type=binding.tracking.active_modifier_only_type,
                    active_modifier_only_shortcut=binding.tracking.active_modifier_only_shortcut,
                    other_key_pressed_during_modifier=binding.tracking.other_key_pressed_during_modifier,
                    is_mode_key_pressed=binding.tracking.is_mode_key_pressed,
                ),
            )
            binding.tracking = ModifierOnlyTrackingState(
                pressed_modifier_key_codes=event.pressed_modifier_key_codes,
                active_modifier_only_type=decision.active_modifier_only_type,
                active_modifier_only_shortcut=decision.active_modifier_only_shortcut,
                other_key_pressed_during_modifier=decision.other_key_pressed_during_modifier,
                is_mode_key_pressed=binding.tracking.is_mode_key_pressed,
            )

            if decision.outcome.kind is OutcomeKind.START:
                self._apply(
                    binding,
                    binding.activation.modifier_only_arm(self._clock(), self._is_recording()),
                )
            elif decision.outcome.kind is OutcomeKind.FINISH:
                self._apply(
                    binding,
                    binding.activation.modifier_only_finish(
                        self._clock(), decision.outcome.was_clean_press
                    ),
                )

    def _handle_key_down(self, event: InputEvent) -> None:
        self._mark_other_input()

        if self.cancel_shortcut is not None and self.cancel_shortcut.matches(
            event.key_code, event.modifiers
        ):
            if self._on_cancel is not None:
                self._on_cancel()
            return

        if (
            self.paste_last_enabled
            and self.paste_last_shortcut is not None
            and self.paste_last_shortcut.matches(event.key_code, event.modifiers)
        ):
            if self._on_paste_last is not None:
                self._on_paste_last()
            return

        for binding in self.bindings:
            if binding.shortcut.is_modifier_only_shortcut or binding.shortcut.is_mouse_shortcut:
                continue
            if binding.enabled and binding.shortcut.matches(event.key_code, event.modifiers):
                self._apply(binding, binding.activation.press(self._clock(), self._is_recording()))

    def _handle_key_up(self, event: InputEvent) -> None:
        for binding in self.bindings:
            if binding.shortcut.is_modifier_only_shortcut or binding.shortcut.is_mouse_shortcut:
                continue
            if binding.enabled and binding.shortcut.key_code == event.key_code:
                self._apply(binding, binding.activation.release(self._clock()))

    def _handle_mouse_down(self, event: InputEvent) -> None:
        # Any click interrupts a modifier-only press: Alt+click is a click,
        # not an Alt tap.
        self._mark_other_input()

        if event.mouse_button is None:
            return
        if (
            self.paste_last_enabled
            and self.paste_last_shortcut is not None
            and self.paste_last_shortcut.matches_mouse(event.mouse_button, event.modifiers)
        ):
            if self._on_paste_last is not None:
                self._on_paste_last()
            return

        for binding in self.bindings:
            if not binding.shortcut.is_mouse_shortcut:
                continue
            if binding.enabled and binding.shortcut.matches_mouse(
                event.mouse_button, event.modifiers
            ):
                self._apply(binding, binding.activation.press(self._clock(), self._is_recording()))

    def _handle_mouse_up(self, event: InputEvent) -> None:
        if event.mouse_button is None:
            return
        for binding in self.bindings:
            if not binding.shortcut.is_mouse_shortcut:
                continue
            if binding.enabled and binding.shortcut.mouse_button == event.mouse_button:
                self._apply(binding, binding.activation.release(self._clock()))

    def _mark_other_input(self) -> None:
        for binding in self.bindings:
            if binding.tracking.active_modifier_only_type is None:
                continue
            binding.tracking = ModifierOnlyTrackingState(
                pressed_modifier_key_codes=binding.tracking.pressed_modifier_key_codes,
                active_modifier_only_type=binding.tracking.active_modifier_only_type,
                active_modifier_only_shortcut=binding.tracking.active_modifier_only_shortcut,
                other_key_pressed_during_modifier=True,
                is_mode_key_pressed=binding.tracking.is_mode_key_pressed,
            )

    def _apply(self, binding: HotkeyBinding, action: ActivationAction) -> None:
        if action is ActivationAction.START:
            if self._on_start is not None:
                self._on_start(binding.hold_mode_type)
        elif action is ActivationAction.STOP:
            if self._on_stop is not None:
                self._on_stop(binding.hold_mode_type)
        elif action is ActivationAction.TOGGLE:
            if self._is_recording():
                if self._on_stop is not None:
                    self._on_stop(binding.hold_mode_type)
            elif self._on_start is not None:
                self._on_start(binding.hold_mode_type)
