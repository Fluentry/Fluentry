"""Finish a dictation by saying a phrase.

A port of `SpokenSendParser`. Saying "send it" at the end of a dictation
strips the phrase and presses the send key; saying "literal send it" types the
words instead.

The `\\p{P}` classes in the original regexes have no equivalent in Python's
`re`, so the punctuation class is built once from the Unicode database, which
gives the same membership rather than an approximation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

#: Settle/quiet thresholds for "send immediately", unchanged from the original.
IMMEDIATE_STOP_SETTLE_DURATION = 1.5
IMMEDIATE_STOP_REQUIRED_SILENCE_DURATION = 0.35
IMMEDIATE_STOP_VOICE_ACTIVITY_GRACE_DURATION = 0.35
IMMEDIATE_STOP_VOICE_ACTIVITY_LEVEL_THRESHOLD = 0.12

TRAILING_PUNCTUATION_TRIM = ",;:-–—"
SENTENCE_TERMINATORS = ".?!…"


@lru_cache(maxsize=1)
def _punctuation_class() -> str:
    """A regex character class matching Unicode general category `P`."""
    characters = []
    for code_point in range(0x0000, 0x3100):
        char = chr(code_point)
        if unicodedata.category(char).startswith("P"):
            characters.append(char)
    return re.escape("".join(characters))


def _separator_pattern() -> str:
    return rf"[\s{_punctuation_class()}]"


@dataclass(frozen=True)
class SpokenSendParseResult:
    text: str
    should_send: bool


def _phrase_pattern(phrase: str) -> str | None:
    words = [re.escape(word) for word in phrase.strip().split() if word]
    if not words:
        return None
    return r"\s+".join(words)


def parse_spoken_send(text: str, phrase: str, enabled: bool) -> SpokenSendParseResult:
    if not enabled:
        return SpokenSendParseResult(text=text, should_send=False)

    phrase_pattern = _phrase_pattern(phrase)
    if phrase_pattern is None:
        return SpokenSendParseResult(text=text, should_send=False)

    separator = _separator_pattern()
    trailing = rf"{separator}*$"

    # "literal send it" escapes the command: keep the words, send nothing.
    literal_regex = re.compile(
        rf"(?<!\w)literal\s+({phrase_pattern}){trailing}", re.IGNORECASE
    )
    literal_match = literal_regex.search(text)
    if literal_match is not None:
        output = text[: literal_match.start()] + literal_match.group(1) + text[literal_match.end() :]
        return SpokenSendParseResult(text=output, should_send=False)

    command_regex = re.compile(
        rf"(?<!\w)({phrase_pattern})(?:{separator}+{phrase_pattern})*{trailing}",
        re.IGNORECASE,
    )
    command_match = command_regex.search(text)
    if command_match is None:
        return SpokenSendParseResult(text=text, should_send=False)

    command_prefix = text[: command_match.start()]
    # A trailing "literal" immediately before the phrase was the escape word
    # for a phrase that also ends the sentence; restore it as the phrase text.
    literal_prefix_regex = re.compile(r"(?<!\w)literal\s*$", re.IGNORECASE)
    literal_prefix_match = literal_prefix_regex.search(command_prefix)
    if literal_prefix_match is not None:
        command_prefix = (
            command_prefix[: literal_prefix_match.start()] + command_match.group(1)
        )

    return SpokenSendParseResult(text=_polish_command_prefix(command_prefix), should_send=True)


def _polish_command_prefix(text: str) -> str:
    """Tidy the sentence the phrase was removed from."""
    polished = text.strip()
    while polished and polished[-1] in TRAILING_PUNCTUATION_TRIM:
        polished = polished[:-1].rstrip()
    if not polished:
        return polished
    if polished[-1] in SENTENCE_TERMINATORS:
        return polished
    return polished + "."


def should_stop_immediately(
    text: str, phrase: str, spoken_send_enabled: bool, send_immediately_enabled: bool
) -> bool:
    if not send_immediately_enabled:
        return False
    return parse_spoken_send(text, phrase=phrase, enabled=spoken_send_enabled).should_send


def can_complete_immediate_stop(
    text: str,
    phrase: str,
    spoken_send_enabled: bool,
    send_immediately_enabled: bool,
    quiet_duration: float,
) -> bool:
    """Require a moment of silence so a mid-sentence "send it" is not acted on."""
    return quiet_duration >= IMMEDIATE_STOP_REQUIRED_SILENCE_DURATION and should_stop_immediately(
        text, phrase, spoken_send_enabled, send_immediately_enabled
    )


def should_cancel_countdown_for_voice_activity(
    countdown_started_at: float, voice_activity_at: float
) -> bool:
    return (
        voice_activity_at - countdown_started_at >= IMMEDIATE_STOP_VOICE_ACTIVITY_GRACE_DURATION
    )


def is_meaningful_voice_activity(level: float) -> bool:
    return level >= IMMEDIATE_STOP_VOICE_ACTIVITY_LEVEL_THRESHOLD
