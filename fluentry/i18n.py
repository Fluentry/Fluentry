"""Interface localisation.

The app speaks around a hundred languages; its *interface* speaks the
eleven this build ships a catalogue for. Everything shown to a person -
labels, buttons, page titles, wizard copy - is wrapped in :func:`tr`,
which returns the active language's translation or, failing that, the
English source it was given. So an untranslated string is never blank; it
simply stays in English.

Catalogues are flat JSON maps of ``English source -> translation`` under
``resources/i18n/<code>.json``. English is the source language and needs
no file. Which language is active is decided once, at startup, from the
stored preference and then the system locale (see :func:`resolve`), and a
change made in Settings takes effect on the next launch - the app already
restarts itself for runtime installs, so the machinery is there.

This module is deliberately free of any Qt or settings import so it can be
used from anywhere, including the tests, without a display.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

#: The languages the interface itself ships, in the order the picker shows
#: them, English first. Each maps its code to its own native name - a
#: picker that lists languages in a language you cannot read is useless.
SUPPORTED_UI_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "it": "Italiano",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
    "hi": "हिन्दी",
    "ar": "العربية",
}

#: Languages that read right-to-left, so the shell can mirror its layout.
RTL_LANGUAGES = frozenset({"ar"})

DEFAULT_LANGUAGE = "en"

_CATALOG_DIRECTORY = Path(__file__).resolve().parent / "resources" / "i18n"

_active_language = DEFAULT_LANGUAGE
_active_catalog: dict[str, str] = {}


def _normalise(code: str | None) -> str | None:
    """Reduce a locale like ``pt_BR.UTF-8`` to a supported code like ``pt``."""
    if not code:
        return None
    # Strip the encoding and any modifier: pt_BR.UTF-8@euro -> pt_BR
    token = code.split(".", 1)[0].split("@", 1)[0].strip()
    if not token or token.upper() in {"C", "POSIX"}:
        return None
    token = token.replace("-", "_")
    lowered = token.lower()
    # Chinese is written zh_CN / zh_TW / zh_Hans; all fold to the one catalogue.
    primary = lowered.split("_", 1)[0]
    if primary in SUPPORTED_UI_LANGUAGES:
        return primary
    return None


def detect_system_language() -> str | None:
    """The interface language the desktop implies, if the app ships it.

    ``LANGUAGE`` may list several codes in preference order (``pt_BR:pt:en``);
    the first one there is a catalogue for wins, then ``LC_ALL`` /
    ``LC_MESSAGES`` / ``LANG``.
    """
    language_list = os.environ.get("LANGUAGE", "")
    for entry in language_list.split(":"):
        found = _normalise(entry)
        if found:
            return found
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        found = _normalise(os.environ.get(name))
        if found:
            return found
    return None


def resolve(preferred: str | None) -> str:
    """The language to actually use: the stored choice, else the locale, else English.

    ``preferred`` is what the user picked in Settings. The sentinel
    ``"system"`` (and ``None``) means "follow the desktop".
    """
    if preferred and preferred != "system":
        normalised = _normalise(preferred) or (
            preferred if preferred in SUPPORTED_UI_LANGUAGES else None
        )
        if normalised:
            return normalised
    return detect_system_language() or DEFAULT_LANGUAGE


@lru_cache(maxsize=len(SUPPORTED_UI_LANGUAGES))
def _load_catalog(code: str) -> dict[str, str]:
    if code == DEFAULT_LANGUAGE:
        return {}
    path = _CATALOG_DIRECTORY / f"{code}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # Keep only real translations; a blank value should fall back to English.
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v}


def set_language(code: str | None) -> str:
    """Make ``code`` the active interface language and return what was set.

    An unknown or unshipped code falls back to English rather than failing,
    so a stored preference from a build that shipped more languages is safe.
    """
    global _active_language, _active_catalog
    chosen = code if code in SUPPORTED_UI_LANGUAGES else DEFAULT_LANGUAGE
    _active_language = chosen
    _active_catalog = _load_catalog(chosen)
    return chosen


def current_language() -> str:
    return _active_language


def is_rtl() -> bool:
    return _active_language in RTL_LANGUAGES


def tr(text: str) -> str:
    """The active language's version of ``text``, or ``text`` unchanged.

    Wrap format templates, not their filled results, so the placeholders
    survive translation:  ``tr("Downloaded {size}").format(size=x)``.
    """
    return _active_catalog.get(text, text)


def language_choices() -> list[tuple[str, str]]:
    """``(code, native name)`` pairs for the Settings picker, English first."""
    return list(SUPPORTED_UI_LANGUAGES.items())
