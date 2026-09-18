"""Spoken punctuation and formatting actions.

Say "literal comma" and get ",". The prefix ("literal" by default) keeps prose
untouched — a bare "comma" in a sentence stays a word.

A direct port of `ASRService+SpokenPunctuationFormatting`.

The macOS source carries an unused second rule table with per-symbol context
requirements ("dot" only becomes "." next to a domain, and so on). The shipped
path builds its rules from the user's dictionary without those flags, because
the prefix already resolves the ambiguity: the user said "literal", so they
meant the symbol. This port keeps the shipped behaviour and omits the
unreachable table.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from ..persistence.settings_types import (
    PunctuationDictionaryRule,
    SpokenFormattingAction,
    SpokenFormattingActionRule,
)


class Spacing(Enum):
    RIGHT_ATTACHED = "rightAttached"
    LEFT_ATTACHED = "leftAttached"
    NO_SPACE_AROUND = "noSpaceAround"
    SPACE_AROUND = "spaceAround"
    TOGGLE_DOUBLE_QUOTE = "toggleDoubleQuote"
    TOGGLE_SINGLE_QUOTE = "toggleSingleQuote"
    LINE_BREAK = "lineBreak"
    PARAGRAPH_BREAK = "paragraphBreak"
    TAB = "tab"
    SINGLE_SPACE = "singleSpace"


_ACTION_SPACING = {
    SpokenFormattingAction.NEW_LINE: Spacing.LINE_BREAK,
    SpokenFormattingAction.NEW_PARAGRAPH: Spacing.PARAGRAPH_BREAK,
    SpokenFormattingAction.TAB: Spacing.TAB,
    SpokenFormattingAction.SPACE: Spacing.SINGLE_SPACE,
}

_FORMATTING_ACTION_SPACINGS = {
    Spacing.LINE_BREAK,
    Spacing.PARAGRAPH_BREAK,
    Spacing.TAB,
    Spacing.SINGLE_SPACE,
}


@dataclass(frozen=True)
class PhraseRule:
    words: tuple[str, ...]
    symbol: str
    spacing: Spacing


@dataclass(frozen=True)
class Token:
    kind: str  # "word" | "text"
    original: str
    normalized: str | None = None

    @property
    def normalized_word(self) -> str | None:
        return self.normalized if self.kind == "word" else None

    @property
    def text(self) -> str:
        return self.original

    @property
    def is_horizontal_whitespace_text(self) -> bool:
        if self.kind != "text" or not self.original:
            return False
        return all(_is_horizontal_whitespace(char) for char in self.original)


@dataclass(frozen=True)
class OutputPart:
    kind: str  # "text" | "punctuation"
    text: str = ""
    symbol: str = ""
    spacing: Spacing | None = None

    @property
    def is_horizontal_whitespace_text(self) -> bool:
        if self.kind != "text" or not self.text:
            return False
        return all(_is_horizontal_whitespace(char) for char in self.text)

    @property
    def is_formatting_action(self) -> bool:
        return self.kind == "punctuation" and self.spacing in _FORMATTING_ACTION_SPACINGS

    @property
    def starts_new_line(self) -> bool:
        return self.kind == "punctuation" and self.spacing in (
            Spacing.LINE_BREAK,
            Spacing.PARAGRAPH_BREAK,
        )


def _is_horizontal_whitespace(char: str) -> bool:
    return char in " \t" or (char.isspace() and char not in "\n\r\v\f  ")


def _is_word_character(char: str) -> bool:
    return char.isalnum()


def _is_ascii_digit(char: str) -> bool:
    return "0" <= char <= "9"


def apply_spoken_punctuation_formatting(
    text: str,
    prefix: str,
    rules: Iterable[PunctuationDictionaryRule],
    action_rules: Iterable[SpokenFormattingActionRule],
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_title: str | None = None,
) -> str:
    """`app_name`/`bundle_id`/`window_title` are accepted so callers can pass
    the focused window through unchanged; punctuation rules are app-agnostic."""
    if not text or prefix.lower() not in text.lower():
        return text

    prefix_words = _words(prefix)
    phrase_rules = _make_rules(rules) + _make_action_rules(action_rules)
    if not prefix_words or not phrase_rules:
        return text

    rules_by_first_word = _grouped_rules_by_first_word(phrase_rules)
    tokens = tokenize(text)
    if not any(token.normalized_word is not None for token in tokens):
        return text

    output: list[OutputPart] = []
    index = 0
    while index < len(tokens):
        match = _match_prefixed_rule(tokens, index, prefix_words, rules_by_first_word)
        if match is not None:
            rule, end_index = match
            output.append(OutputPart("punctuation", symbol=rule.symbol, spacing=rule.spacing))
            index = end_index
        else:
            output.append(OutputPart("text", text=tokens[index].text))
            index += 1

    without_commas = _removing_generated_comma_noise(output)
    return _render(_removing_generated_sentence_punctuation_beside_actions(without_commas))


def _grouped_rules_by_first_word(rules: list[PhraseRule]) -> dict[str, list[PhraseRule]]:
    grouped: dict[str, list[PhraseRule]] = {}
    for rule in rules:
        first = rule.words[0] if rule.words else ""
        grouped.setdefault(first, []).append(rule)
    for key, group in grouped.items():
        # Longest phrase first so "full stop" beats "full".
        group.sort(key=lambda rule: (len(rule.words), len(" ".join(rule.words))), reverse=True)
    return grouped


def _make_rules(dictionary_rules: Iterable[PunctuationDictionaryRule]) -> list[PhraseRule]:
    result: list[PhraseRule] = []
    for rule in dictionary_rules:
        result.extend(_rules_for(rule.symbol, _spacing_for(rule), rule.aliases, rule))
    return result


def _make_action_rules(action_rules: Iterable[SpokenFormattingActionRule]) -> list[PhraseRule]:
    result: list[PhraseRule] = []
    for rule in action_rules:
        if not rule.is_enabled or not rule.aliases:
            continue
        result.extend(_rules_for(rule.action.output, _ACTION_SPACING[rule.action], rule.aliases, None))
    return result


def _spacing_for(rule: PunctuationDictionaryRule) -> Spacing:
    aliases = set(rule.aliases)
    symbol = rule.symbol
    if symbol == ".":
        return Spacing.NO_SPACE_AROUND if "dot" in aliases else Spacing.RIGHT_ATTACHED
    if symbol in {",", "?", "!", ":", ";", "...", ")", "]", "}", ">", "%"}:
        return Spacing.RIGHT_ATTACHED
    if symbol in {"(", "[", "{", "<", "$"}:
        return Spacing.LEFT_ATTACHED
    if symbol in {"+", "=", "&", "—", "–"}:
        return Spacing.SPACE_AROUND
    if symbol == "-":
        return Spacing.SPACE_AROUND if ("dash" in aliases or "minus sign" in aliases) else Spacing.NO_SPACE_AROUND
    if symbol == '"':
        if any(alias.startswith("open ") or alias.startswith("opening ") for alias in aliases):
            return Spacing.LEFT_ATTACHED
        if any(alias.startswith("close ") or alias.startswith("closing ") for alias in aliases):
            return Spacing.RIGHT_ATTACHED
        return Spacing.TOGGLE_DOUBLE_QUOTE
    if symbol == "'":
        return Spacing.NO_SPACE_AROUND if "apostrophe" in aliases else Spacing.TOGGLE_SINGLE_QUOTE
    return Spacing.NO_SPACE_AROUND


def _rules_for(
    symbol: str,
    spacing: Spacing,
    phrases: Iterable[str],
    source: PunctuationDictionaryRule | None = None,
) -> list[PhraseRule]:
    rules: list[PhraseRule] = []
    for phrase in phrases:
        words = tuple(word for word in phrase.lower().split(" ") if word)
        if not words:
            continue
        rules.append(PhraseRule(words=words, symbol=symbol, spacing=spacing))
    return rules


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    current = ""
    building_word = False

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if building_word:
            tokens.append(Token("word", current, current.lower()))
        else:
            tokens.append(Token("text", current))
        current = ""

    for char in text:
        is_word = _is_word_character(char)
        if not current:
            current = char
            building_word = is_word
        elif is_word == building_word:
            current += char
        else:
            flush()
            current = char
            building_word = is_word
    flush()
    return tokens


def _match_prefixed_rule(
    tokens: list[Token],
    index: int,
    prefix_words: list[str],
    rules_by_first_word: dict[str, list[PhraseRule]],
) -> tuple[PhraseRule, int] | None:
    alias_index = _index_after_prefix(prefix_words, tokens, index)
    if alias_index is None:
        return None
    return _match_rule(tokens, alias_index, rules_by_first_word)


def _index_after_prefix(prefix_words: list[str], tokens: list[Token], index: int) -> int | None:
    if not prefix_words:
        return None
    cursor = index
    for word_index, prefix_word in enumerate(prefix_words):
        if word_index > 0:
            if cursor >= len(tokens) or not tokens[cursor].is_horizontal_whitespace_text:
                return None
            while cursor < len(tokens) and tokens[cursor].is_horizontal_whitespace_text:
                cursor += 1
        if cursor >= len(tokens) or tokens[cursor].normalized_word != prefix_word:
            return None
        cursor += 1

    if cursor >= len(tokens) or not tokens[cursor].is_horizontal_whitespace_text:
        return None
    while cursor < len(tokens) and tokens[cursor].is_horizontal_whitespace_text:
        cursor += 1
    return cursor if cursor < len(tokens) else None


def _match_rule(
    tokens: list[Token],
    index: int,
    rules_by_first_word: dict[str, list[PhraseRule]],
) -> tuple[PhraseRule, int] | None:
    first_word = tokens[index].normalized_word
    if first_word is None:
        return None
    candidates = rules_by_first_word.get(first_word)
    if not candidates:
        return None

    for rule in candidates:
        cursor = index
        matched = True
        for word_index, expected in enumerate(rule.words):
            if word_index > 0:
                if cursor >= len(tokens) or not tokens[cursor].is_horizontal_whitespace_text:
                    matched = False
                    break
                while cursor < len(tokens) and tokens[cursor].is_horizontal_whitespace_text:
                    cursor += 1
            if cursor >= len(tokens) or tokens[cursor].normalized_word != expected:
                matched = False
                break
            cursor += 1
        if not matched:
            continue
        return (rule, cursor)
    return None


def _significant_part_index_before(parts: list, index: int) -> int | None:
    cursor = index - 1
    while cursor >= 0:
        if not parts[cursor].is_horizontal_whitespace_text:
            return cursor
        cursor -= 1
    return None


# --- cleanup ---------------------------------------------------------------


def _removing_generated_comma_noise(parts: list[OutputPart]) -> list[OutputPart]:
    if not any(part.kind == "punctuation" and part.symbol == "," for part in parts):
        return parts
    result: list[OutputPart] = []
    for index, part in enumerate(parts):
        if part.kind == "punctuation" and part.symbol == "," and _should_remove_generated_comma(index, parts):
            continue
        result.append(part)
    return result


def _removing_generated_sentence_punctuation_beside_actions(
    parts: list[OutputPart],
) -> list[OutputPart]:
    cleaned = list(parts)
    for index, part in enumerate(cleaned):
        if not part.is_formatting_action:
            continue
        if index > 0 and cleaned[index - 1].kind == "text":
            cleaned[index - 1] = replace(
                cleaned[index - 1], text=_removing_trailing_generated_period(cleaned[index - 1].text)
            )
        if index + 1 < len(cleaned) and cleaned[index + 1].kind == "text":
            cleaned[index + 1] = replace(
                cleaned[index + 1],
                text=_removing_leading_generated_punctuation(
                    cleaned[index + 1].text, includes_comma=part.starts_new_line
                ),
            )
    return cleaned


def _removing_trailing_generated_period(text: str) -> str:
    cleaned = text
    while cleaned and _is_horizontal_whitespace(cleaned[-1]):
        cleaned = cleaned[:-1]
    if not cleaned.endswith("."):
        return text
    # "..." is deliberate, a single "." beside an action is ASR noise.
    if len(cleaned) >= 2 and cleaned[-2] == ".":
        return text
    return cleaned[:-1]


def _removing_leading_generated_punctuation(text: str, includes_comma: bool) -> str:
    index = 0
    while index < len(text) and _is_horizontal_whitespace(text[index]):
        index += 1
    if index >= len(text):
        return text
    punctuation = text[index]
    if not (punctuation == "." or (includes_comma and punctuation == ",")):
        return text
    after = index + 1
    if after < len(text) and text[after] == punctuation:
        return text
    remainder = after
    while remainder < len(text) and _is_horizontal_whitespace(text[remainder]):
        remainder += 1
    return text[remainder:]


def _should_remove_generated_comma(index: int, parts: list[OutputPart]) -> bool:
    previous = _significant_part_before(parts, index)
    following = _significant_part_after(parts, index)

    if _is_generated_punctuation_pair(previous, following):
        return True
    if (
        following is not None
        and following.kind == "punctuation"
        and following.symbol == "%"
        and _part_ends_with_ascii_digit(previous)
    ):
        return True
    return False


def _significant_part_before(parts: list[OutputPart], index: int) -> OutputPart | None:
    cursor = index - 1
    while cursor >= 0:
        if not parts[cursor].is_horizontal_whitespace_text:
            return parts[cursor]
        cursor -= 1
    return None


def _significant_part_after(parts: list[OutputPart], index: int) -> OutputPart | None:
    cursor = index + 1
    while cursor < len(parts):
        if not parts[cursor].is_horizontal_whitespace_text:
            return parts[cursor]
        cursor += 1
    return None


def _is_generated_punctuation_pair(previous: OutputPart | None, following: OutputPart | None) -> bool:
    if previous is None or following is None:
        return False
    if previous.kind != "punctuation" or following.kind != "punctuation":
        return False
    if not previous.symbol or not following.symbol:
        return False
    return (
        previous.symbol[0] in PUNCTUATION_PAIR_COMMA_CLEANUP_CHARACTERS
        and following.symbol[0] in PUNCTUATION_PAIR_COMMA_CLEANUP_CHARACTERS
    )


def _part_ends_with_ascii_digit(part: OutputPart | None) -> bool:
    if part is None or part.kind != "text" or not part.text:
        return False
    return _is_ascii_digit(part.text[-1])


# --- rendering -------------------------------------------------------------


def _render(parts: list[OutputPart]) -> str:
    result = ""
    index = 0
    should_open_double_quote = True
    should_open_single_quote = True

    while index < len(parts):
        part = parts[index]
        if part.kind == "text":
            result += part.text
            index += 1
            continue

        spacing = part.spacing
        if spacing is Spacing.TOGGLE_DOUBLE_QUOTE:
            spacing = Spacing.LEFT_ATTACHED if should_open_double_quote else Spacing.RIGHT_ATTACHED
            should_open_double_quote = not should_open_double_quote
        elif spacing is Spacing.TOGGLE_SINGLE_QUOTE:
            spacing = Spacing.LEFT_ATTACHED if should_open_single_quote else Spacing.RIGHT_ATTACHED
            should_open_single_quote = not should_open_single_quote

        if spacing is Spacing.RIGHT_ATTACHED:
            result = _remove_trailing_horizontal_whitespace(result)
            result += part.symbol
            index += 1
        elif spacing is Spacing.LEFT_ATTACHED:
            result += part.symbol
            index = _index_skipping_whitespace(parts, index)
        elif spacing is Spacing.NO_SPACE_AROUND:
            result = _remove_trailing_horizontal_whitespace(result)
            result += part.symbol
            index = _index_skipping_whitespace(parts, index)
        elif spacing is Spacing.SPACE_AROUND:
            result = _remove_trailing_horizontal_whitespace(result)
            if result and result[-1] not in "\n\r":
                result += " "
            result += part.symbol
            index = _index_skipping_whitespace(parts, index)
            if _has_following_non_whitespace_part(parts, index):
                result += " "
        elif spacing is Spacing.LINE_BREAK:
            result = _remove_trailing_horizontal_whitespace(result)
            result += "\n"
            index = _index_skipping_whitespace(parts, index)
        elif spacing is Spacing.PARAGRAPH_BREAK:
            result = _remove_trailing_horizontal_whitespace(result)
            result += "\n\n"
            index = _index_skipping_whitespace(parts, index)
        elif spacing is Spacing.TAB:
            result = _remove_trailing_horizontal_whitespace(result)
            result += "\t"
            index = _index_skipping_whitespace(parts, index)
        elif spacing is Spacing.SINGLE_SPACE:
            result = _remove_trailing_horizontal_whitespace(result)
            result += " "
            index = _index_skipping_whitespace(parts, index)
        else:
            index += 1

    return result


def _remove_trailing_horizontal_whitespace(text: str) -> str:
    while text and _is_horizontal_whitespace(text[-1]):
        text = text[:-1]
    return text


def _index_skipping_whitespace(parts: list[OutputPart], index: int) -> int:
    next_index = index + 1
    while next_index < len(parts):
        part = parts[next_index]
        if part.kind != "text" or not all(_is_horizontal_whitespace(char) for char in part.text):
            break
        next_index += 1
    return next_index


def _has_following_non_whitespace_part(parts: list[OutputPart], index: int) -> bool:
    for part in parts[index:]:
        if part.kind == "text":
            if any(not _is_horizontal_whitespace(char) for char in part.text):
                return True
        else:
            return True
    return False


def _words(phrase: str) -> list[str]:
    return [word.lower() for word in phrase.split() if word]


PUNCTUATION_PAIR_COMMA_CLEANUP_CHARACTERS = set("+=%-—–/\\@#$&*_|~^<>()[]{}\"'`.?!:;")
