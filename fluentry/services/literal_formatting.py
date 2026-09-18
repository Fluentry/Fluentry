"""Literal dictation formatting: slash commands and @-mentions.

"run slash deploy" becomes "/deploy"; "ping At Sam" becomes "@Sam" — but only
where that reading is unambiguous. A port of
`ASRService+DictationLiteralFormatting`, including the lead-in word lists and
rejection lists that keep ordinary prose ("meet at noon") untouched.

The app lists gain the Linux terminals and editors that play the same role as
their macOS counterparts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DictationLiteralOutputPlan:
    steps: tuple[str, ...]

    @property
    def plain_text(self) -> str:
        return "".join(self.steps)

    @staticmethod
    def plain(text: str) -> "DictationLiteralOutputPlan":
        return DictationLiteralOutputPlan((text,))


# `(?<!\w)` is Python's equivalent of the original `(?<![\p{L}\p{N}_])`.
SLASH_COMMAND_LITERAL_RE = re.compile(
    r"(?<!\w)/\s+([A-Za-z][A-Za-z0-9_-]{1,39})(?![A-Za-z0-9_-])"
)

SLASH_COMMAND_SPOKEN_RE = re.compile(
    r"(?<!\w)(?:forward\s+slash|slash)\s+([A-Za-z][A-Za-z0-9_-]{1,39})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)

STANDALONE_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]{1,39}$")

EXPLICIT_MENTION_RE = re.compile(
    r"(?<![\w@])(?i:(?:at\s+(?:sign|the\s+rate)|tag|mention))"
    r"\s+([A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Za-z0-9_.-]*){0,2})(?![A-Za-z0-9_.-])"
)

RELAXED_MENTION_RE = re.compile(
    r"(?<![\w@])[Aa]t\s+([A-Z][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Za-z0-9_.-]*){0,2})(?![A-Za-z0-9_.-])"
)

TERMINAL_MENTION_TOKEN_RE = re.compile(
    r"(?<![\w@])@([A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Za-z0-9_.-]*){0,2})$"
)

SLASH_COMMAND_REJECTED_TOKENS = {
    "a", "an", "and", "as", "at", "back", "backslash", "be", "been", "being",
    "bin", "by", "comma", "desktop", "documents", "dot", "downloads", "etc",
    "for", "forward", "from", "home", "in", "is", "library", "local", "mark",
    "of", "on", "or", "period", "private", "question", "quote", "quotes",
    "semicolon", "slash", "slashes", "source", "sources", "src", "the", "tmp",
    "to", "user", "users", "usr", "var", "volumes", "was", "were", "with",
    "without",
    # Linux root directories, for the same reason "usr" and "var" are listed.
    "boot", "dev", "media", "mnt", "opt", "proc", "root", "run", "srv", "sys",
}

SLASH_COMMAND_SPOKEN_LEAD_IN_WORDS = {
    "call", "choose", "do", "enter", "execute", "open", "pick", "press", "run",
    "say", "select", "send", "start", "try", "type", "use", "write",
}

MENTION_REJECTED_TOKENS = {
    "a", "an", "airport", "breakfast", "brunch", "class", "dinner", "home",
    "hotel", "house", "lunch", "meeting", "night", "noon", "office", "place",
    "restaurant", "school", "shop", "store", "the", "today", "tomorrow",
    "work", "yesterday",
}

RELAXED_MENTION_LEAD_IN_WORDS = {
    "add", "ask", "cc", "dm", "hello", "hey", "hi", "invite", "message",
    "notify", "ping", "send", "tag", "tell",
}

#: Chat apps where a bare "at Sam" nearly always means a mention.
RELAXED_MENTION_APPS = ("slack", "discord", "teams", "element", "matrix", "mattermost")

#: Assistants whose composer autocompletes a slash command on the space key.
SLASH_COMMAND_AUTOCOMPLETE_APPS = ("codex", "chatgpt", "claude", "cursor", "windsurf")

SENTENCE_BOUNDARY_CHARACTERS = ".!?:;([{"


def _is_ascii_alphabetic(char: str) -> bool:
    return ("A" <= char <= "Z") or ("a" <= char <= "z")


def _is_ascii_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _is_command_token_character(char: str) -> bool:
    return _is_ascii_alphabetic(char) or _is_ascii_digit(char) or char in "-_"


def _is_mention_token_character(char: str) -> bool:
    return _is_ascii_alphabetic(char) or _is_ascii_digit(char) or char in "-_."


def _is_horizontal_whitespace(char: str) -> bool:
    return char in " \t" or (char.isspace() and char not in "\n\r\v\f")


def _haystack(app_name: str | None, bundle_id: str | None, window_title: str | None) -> str:
    return " ".join(value.lower() for value in (app_name, bundle_id, window_title) if value)


def is_relaxed_mention_app(
    app_name: str | None = None, bundle_id: str | None = None, window_title: str | None = None
) -> bool:
    haystack = _haystack(app_name, bundle_id, window_title)
    return any(needle in haystack for needle in RELAXED_MENTION_APPS)


def is_slash_command_autocomplete_app(
    app_name: str | None = None, bundle_id: str | None = None, window_title: str | None = None
) -> bool:
    haystack = _haystack(app_name, bundle_id, window_title)
    return any(needle in haystack for needle in SLASH_COMMAND_AUTOCOMPLETE_APPS)


# --- slash commands --------------------------------------------------------


def apply_slash_command_formatting(text: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    if not text or ("/" not in text and "slash" not in text.lower()):
        return text
    literal_formatted = _replacing_slash_command_matches(text, SLASH_COMMAND_LITERAL_RE, spoken=False)
    return _replacing_slash_command_matches(literal_formatted, SLASH_COMMAND_SPOKEN_RE, spoken=True)


def _replacing_slash_command_matches(text: str, pattern: re.Pattern, spoken: bool) -> str:
    matches = list(pattern.finditer(text))
    if not matches:
        return text
    result = text
    for match in reversed(matches):
        token = match.group(1)
        if not _is_valid_slash_command_token(token):
            continue
        if spoken and not _has_spoken_slash_command_context(text, match.start()):
            continue
        result = result[: match.start()] + "/" + token.lower() + result[match.end() :]
    return result


def _is_valid_slash_command_token(token: str) -> bool:
    lowercased = token.lower()
    if lowercased in SLASH_COMMAND_REJECTED_TOKENS:
        return False
    if not lowercased or not _is_ascii_alphabetic(lowercased[0]):
        return False
    if not _is_command_token_character(lowercased[-1]):
        return False
    return all(_is_command_token_character(char) for char in lowercased)


def _has_spoken_slash_command_context(text: str, match_location: int) -> bool:
    if match_location <= 0:
        return True
    prefix = text[:match_location].strip()
    if not prefix:
        return True
    if prefix[-1] in SENTENCE_BOUNDARY_CHARACTERS:
        return True
    words = _split_into_token_words(prefix)
    if not words:
        return True
    return words[-1].lower() in SLASH_COMMAND_SPOKEN_LEAD_IN_WORDS


def _split_into_token_words(text: str) -> list[str]:
    words: list[str] = []
    current = ""
    for char in text:
        if _is_ascii_alphabetic(char) or _is_ascii_digit(char) or char in "-_":
            current += char
        elif current:
            words.append(current)
            current = ""
    if current:
        words.append(current)
    return words


# --- mentions --------------------------------------------------------------


def apply_mention_formatting(
    text: str,
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_title: str | None = None,
    enabled: bool = True,
) -> str:
    if not enabled:
        return text
    lowered = text.lower()
    if not text or not ("at " in lowered or "tag " in lowered or "mention " in lowered):
        return text

    explicit = _replacing_mention_matches(text, EXPLICIT_MENTION_RE, relaxed=False)
    if not is_relaxed_mention_app(app_name, bundle_id, window_title):
        return explicit
    return _replacing_mention_matches(explicit, RELAXED_MENTION_RE, relaxed=True)


def _replacing_mention_matches(text: str, pattern: re.Pattern, relaxed: bool) -> str:
    matches = list(pattern.finditer(text))
    if not matches:
        return text
    result = text
    for match in reversed(matches):
        name = match.group(1)
        if not _is_valid_mention_name(name, relaxed=relaxed):
            continue
        if _is_possessive_mention_match(text, match.end()):
            continue
        if relaxed and not _has_relaxed_mention_context(text, match.start()):
            continue
        result = result[: match.start()] + "@" + name.strip(" \t") + result[match.end() :]
    return result


def _is_valid_mention_name(name: str, relaxed: bool) -> bool:
    tokens = [token.strip() for token in name.split(" ") if token.strip()]
    if not tokens or len(tokens) > 3:
        return False
    for token in tokens:
        if token.lower() in MENTION_REJECTED_TOKENS:
            return False
        if not all(_is_mention_token_character(char) for char in token):
            return False
        if relaxed and not (_is_ascii_alphabetic(token[0]) and token[0].isupper()):
            return False
    return True


def _is_possessive_mention_match(text: str, end: int) -> bool:
    if end >= len(text):
        return False
    return text[end] in ("'", "’")


def _has_relaxed_mention_context(text: str, match_location: int) -> bool:
    if match_location <= 0:
        return True
    prefix = text[:match_location].strip()
    if not prefix:
        return True
    if prefix[-1] in SENTENCE_BOUNDARY_CHARACTERS:
        return True
    words = _split_into_token_words(prefix)
    if not words:
        return True
    return words[-1].lower() in RELAXED_MENTION_LEAD_IN_WORDS


# --- combined --------------------------------------------------------------


def apply_dictation_literal_formatting(
    text: str,
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_title: str | None = None,
    enabled: bool = True,
) -> str:
    if not enabled:
        return text
    command_formatted = apply_slash_command_formatting(text, enabled=True)
    return apply_mention_formatting(
        command_formatted,
        app_name=app_name,
        bundle_id=bundle_id,
        window_title=window_title,
        enabled=True,
    )


def apply_terminal_literal_autocomplete_spacing(
    text: str,
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_title: str | None = None,
    enabled: bool = True,
) -> str:
    """Drop a trailing space that would confirm an app's autocomplete popup.

    Typing "/deploy " into an assistant composer accepts whatever the popup
    highlighted; leaving the space off keeps the user in control.
    """
    if not enabled:
        return text
    if not text or not _is_horizontal_whitespace(text[-1]):
        return text

    trimmed = text
    while trimmed and _is_horizontal_whitespace(trimmed[-1]):
        trimmed = trimmed[:-1]
    if not trimmed:
        return text

    if is_slash_command_autocomplete_app(app_name, bundle_id, window_title) and STANDALONE_SLASH_COMMAND_RE.fullmatch(
        trimmed
    ):
        return trimmed

    if is_relaxed_mention_app(app_name, bundle_id, window_title) and TERMINAL_MENTION_TOKEN_RE.search(trimmed):
        return trimmed

    return text


def make_dictation_literal_output_plan(
    text: str,
    app_name: str | None = None,
    bundle_id: str | None = None,
    window_title: str | None = None,
    enabled: bool = True,
) -> DictationLiteralOutputPlan:
    return DictationLiteralOutputPlan.plain(
        apply_terminal_literal_autocomplete_spacing(
            text,
            app_name=app_name,
            bundle_id=bundle_id,
            window_title=window_title,
            enabled=enabled,
        )
    )
