"""Port of SpokenSendTests."""

import math

import pytest

from fluentry.models.keycodes import ModifierFlags
from fluentry.persistence.settings_types import SpokenSendKey
from fluentry.services.spoken_send import (
    IMMEDIATE_STOP_REQUIRED_SILENCE_DURATION,
    IMMEDIATE_STOP_VOICE_ACTIVITY_GRACE_DURATION,
    IMMEDIATE_STOP_VOICE_ACTIVITY_LEVEL_THRESHOLD,
    SpokenSendParseResult,
    can_complete_immediate_stop,
    is_meaningful_voice_activity,
    parse_spoken_send,
    should_cancel_countdown_for_voice_activity,
    should_stop_immediately,
)


def parse(text: str, phrase: str = "send it", enabled: bool = True) -> SpokenSendParseResult:
    return parse_spoken_send(text, phrase=phrase, enabled=enabled)


def result(text: str, should_send: bool) -> SpokenSendParseResult:
    return SpokenSendParseResult(text=text, should_send=should_send)


def test_disabled_feature_leaves_text_untouched():
    assert parse("Hello send it", enabled=False) == result("Hello send it", False)


def test_terminal_phrase_is_removed_and_arms_send():
    assert parse("Hello there, send it.") == result("Hello there.", True)


def test_capitalization_and_full_stop_do_not_affect_send():
    assert parse("Ready to go, SEND IT.") == result("Ready to go.", True)


def test_nearby_trailing_punctuation_does_not_affect_send():
    assert parse('Ready to go — send it…")]') == result("Ready to go.", True)


def test_phrase_in_middle_does_not_arm_send():
    assert parse("Send it when you are ready") == result("Send it when you are ready", False)


def test_terminal_phrase_does_not_require_punctuation():
    assert parse("Ready to go send it") == result("Ready to go.", True)


def test_repeated_terminal_phrases_are_all_removed():
    assert parse("I wanna send it, send it.") == result("I wanna.", True)
    assert parse("Ready SEND IT send it") == result("Ready.", True)
    assert parse("send it, send it.") == result("", True)


def test_repeated_trailing_separators_collapse_to_one_sentence_ending():
    assert parse("Ready,,,,; — send it") == result("Ready.", True)
    assert parse("Ready.,,,;— send it, send it.") == result("Ready.", True)
    assert parse("Ready?,,, send it") == result("Ready?", True)


def test_final_question_or_exclamation_mark_is_preserved():
    assert parse("Are we ready? send it.") == result("Are we ready?", True)
    assert parse("Ship it! send it.") == result("Ship it!", True)


def test_literal_escape_keeps_phrase_without_sending():
    assert parse("Please type literal send it.") == result("Please type send it", False)


def test_literal_escape_before_repeated_command_keeps_one_phrase_and_sends():
    assert parse("Please type literal send it, send it.") == result("Please type send it.", True)


def test_phrase_only_submits_existing_draft_without_inserting_command():
    assert parse("Send it") == result("", True)


def test_custom_phrase_allows_flexible_whitespace_and_case():
    assert parse("Looks good. PLEASE   SUBMIT", phrase="please submit") == result(
        "Looks good.", True
    )


def test_a_blank_phrase_never_arms_send():
    assert parse("anything at all", phrase="   ") == result("anything at all", False)


# --- immediate stop ---------------------------------------------------------


def test_immediate_stop_requires_the_child_option():
    assert should_stop_immediately(
        "Ready, send it.", "send it", spoken_send_enabled=True, send_immediately_enabled=True
    )
    assert not should_stop_immediately(
        "Ready, send it.", "send it", spoken_send_enabled=True, send_immediately_enabled=False
    )


def test_immediate_stop_does_not_trigger_for_a_phrase_in_the_middle():
    assert not should_stop_immediately(
        "Send it when you are ready",
        "send it",
        spoken_send_enabled=True,
        send_immediately_enabled=True,
    )


def test_terminal_asr_refinement_stays_armed():
    for text in ("Ready, send it", "Ready, SEND IT."):
        assert should_stop_immediately(
            text, "send it", spoken_send_enabled=True, send_immediately_enabled=True
        ), text
    assert not should_stop_immediately(
        "Ready, send it after I finish this sentence.",
        "send it",
        spoken_send_enabled=True,
        send_immediately_enabled=True,
    )


def test_immediate_stop_completion_requires_terminal_phrase_and_silence():
    assert not can_complete_immediate_stop(
        "Ready, send it.",
        "send it",
        spoken_send_enabled=True,
        send_immediately_enabled=True,
        quiet_duration=0,
    )
    assert can_complete_immediate_stop(
        "Ready, send it.",
        "send it",
        spoken_send_enabled=True,
        send_immediately_enabled=True,
        quiet_duration=IMMEDIATE_STOP_REQUIRED_SILENCE_DURATION,
    )


def test_immediate_stop_completion_cancels_for_continued_speech():
    assert not can_complete_immediate_stop(
        "Ready, send it after I finish this sentence.",
        "send it",
        spoken_send_enabled=True,
        send_immediately_enabled=True,
        quiet_duration=2,
    )


def test_voice_activity_grace_ignores_only_the_recognition_tail():
    started_at = 100.0
    assert not should_cancel_countdown_for_voice_activity(started_at, started_at + 0.05)
    assert should_cancel_countdown_for_voice_activity(
        started_at, started_at + IMMEDIATE_STOP_VOICE_ACTIVITY_GRACE_DURATION + 0.001
    )
    assert not is_meaningful_voice_activity(
        math.nextafter(IMMEDIATE_STOP_VOICE_ACTIVITY_LEVEL_THRESHOLD, 0)
    )
    assert is_meaningful_voice_activity(IMMEDIATE_STOP_VOICE_ACTIVITY_LEVEL_THRESHOLD)


# --- send key ---------------------------------------------------------------


def test_available_send_commands_map_to_expected_modifiers():
    assert SpokenSendKey.ENTER.modifier_flags == ModifierFlags.NONE
    assert SpokenSendKey.SHIFT_ENTER.modifier_flags == ModifierFlags.SHIFT
    # macOS used Command+Enter; Ctrl+Enter is the Linux chord for the same thing.
    assert SpokenSendKey.COMMAND_ENTER.modifier_flags == ModifierFlags.CONTROL
    assert SpokenSendKey.COMMAND_ENTER.display_name == "Ctrl + Enter"
