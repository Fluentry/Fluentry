"""Custom dictionary replacement.

Maps misheard spellings onto the right text ("fluent tree" → "Fluentry") and
supports whitespace payloads ("new line" → an actual newline). A port of the
dictionary half of `ASRService`, plus the manual-entry helpers from
`CustomDictionaryView`.

Two rules are load-bearing:

* the replacement is **literal** — `$5 \\path` must survive unchanged, so it is
  never interpreted as a regex template,
* a whitespace-only replacement **consumes the spaces around its trigger**, so
  "first  new line  second" becomes "first\\nsecond" rather than
  "first \\n second".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..persistence.settings_types import CustomDictionaryEntry


@dataclass(frozen=True)
class CompiledDictionaryPattern:
    regex: re.Pattern
    replacement: str
    #: Set for a deletion, where the spaces on either side of the trigger
    #: have to collapse into one rather than both disappearing.
    is_deletion: bool = False


def _is_word_character(char: str) -> bool:
    return char.isalnum() or char == "_"


def dictionary_pattern(
    trigger: str,
    consumes_horizontal_separators: bool = False,
    captures_separators: bool = False,
) -> str:
    """The regex for one trigger.

    `\b` is only applied where the trigger's edge is a word character: a
    punctuation-only trigger such as "¿" has no word boundary beside it, so
    wrapping it would stop it ever matching.
    """
    escaped = re.escape(trigger)
    prefix = r"\b" if trigger and _is_word_character(trigger[0]) else ""
    suffix = r"\b" if trigger and _is_word_character(trigger[-1]) else ""
    if captures_separators:
        # Captured, so a deletion can see whether it had a space on each side.
        return f"([ \t]*){prefix}{escaped}{suffix}([ \t]*)"
    separator = r"[ \t]*" if consumes_horizontal_separators else ""
    return f"{separator}{prefix}{escaped}{suffix}{separator}"


def compile_dictionary(entries: Iterable[CustomDictionaryEntry]) -> list[CompiledDictionaryPattern]:
    patterns: list[CompiledDictionaryPattern] = []
    for entry in entries:
        # An empty replacement deletes the trigger. Upstream leaves the
        # spaces behind, so "I basically agree" becomes "I  agree"; here the
        # surrounding spaces are captured and collapsed back into one.
        is_deletion = entry.replacement == ""
        # A whitespace-only replacement swallows the spaces around it, so
        # "a new line b" becomes "a\nb" rather than "a \n b".
        consumes_separators = bool(entry.replacement) and entry.replacement.strip() == ""
        for trigger in entry.triggers:
            if not trigger:
                continue
            pattern = dictionary_pattern(
                trigger, consumes_separators, captures_separators=is_deletion
            )
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                continue
            patterns.append(
                CompiledDictionaryPattern(
                    regex=regex, replacement=entry.replacement, is_deletion=is_deletion
                )
            )
    # Longest pattern first, so ",," wins over "," on the same input.
    patterns.sort(key=lambda item: len(item.regex.pattern), reverse=True)
    return patterns


class CustomDictionary:
    """Caches the compiled patterns; invalidated whenever entries change."""

    def __init__(self, entries: Sequence[CustomDictionaryEntry] | None = None) -> None:
        self._entries: list[CustomDictionaryEntry] = list(entries or [])
        self._patterns: list[CompiledDictionaryPattern] | None = None

    @property
    def entries(self) -> list[CustomDictionaryEntry]:
        return list(self._entries)

    @entries.setter
    def entries(self, value: Sequence[CustomDictionaryEntry]) -> None:
        self._entries = list(value)
        self.invalidate()

    def invalidate(self) -> None:
        self._patterns = None

    @property
    def patterns(self) -> list[CompiledDictionaryPattern]:
        if self._patterns is None:
            self._patterns = compile_dictionary(self._entries)
        return self._patterns

    def apply(self, text: str) -> str:
        if not self._entries:
            return text
        patterns = self.patterns
        if not patterns:
            return text
        result = text
        for pattern in patterns:
            if pattern.is_deletion:
                result = pattern.regex.sub(_deletion, result)
                continue
            # `lambda _: replacement` keeps the replacement literal; `re.sub`
            # would otherwise read "\\path" and "$5" as escapes.
            result = pattern.regex.sub(lambda _match, value=pattern.replacement: value, result)
        return result


def _deletion(match: "re.Match") -> str:
    """Remove the trigger, leaving one space where it sat between words."""
    before, after = match.group(1), match.group(2)
    return " " if before and after else ""


def apply_custom_dictionary(text: str, entries: Iterable[CustomDictionaryEntry]) -> str:
    """Convenience wrapper for callers without a cached dictionary."""
    return CustomDictionary(list(entries)).apply(text)


# --- manual entry helpers ---------------------------------------------------

WHITESPACE_DISPLAY_SYMBOLS = {"\n": "⏎", " ": "␣", "\t": "⇥", "\r": "⏎"}


class CustomDictionaryManualEntry:
    @staticmethod
    def sanitized_replacement(text: str) -> str:
        """Trim padding, but keep an intentional all-whitespace payload."""
        trimmed = text.strip()
        return text if trimmed == "" else trimmed

    @staticmethod
    def replacement_display_text(text: str) -> str:
        """Render whitespace payloads visibly so a row is never blank."""
        if text and text.strip() == "":
            return "".join(WHITESPACE_DISPLAY_SYMBOLS.get(char, char) for char in text)
        return text


def dictionary_labels(entries: Iterable[CustomDictionaryEntry]) -> dict[str, str]:
    """Entry id → replacement, with the last duplicate id winning."""
    labels: dict[str, str] = {}
    for entry in entries:
        labels[entry.id] = entry.replacement
    return labels
