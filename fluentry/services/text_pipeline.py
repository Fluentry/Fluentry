"""The transformations applied between raw ASR output and typed text.

The steps a raw transcript passes through, in the order they run:

1. filler-word removal,
2. custom dictionary replacement,
3. spoken punctuation,
4. *(optional AI enhancement happens here)*,
5. literal formatting (slash commands and mentions),
6. GAAV formatting,
7. continuous-dictation spacing and capitalization.

Keeping the order explicit matters: dictionary replacement must run before
punctuation so a replacement can contain a spoken-punctuation alias, and GAAV
must run after literal formatting so it lowercases the final first letter.
"""

from __future__ import annotations

import string
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..persistence.settings_store import SettingsStore
from .custom_dictionary import CustomDictionary
from .literal_formatting import apply_dictation_literal_formatting, make_dictation_literal_output_plan
from .spoken_punctuation import apply_spoken_punctuation_formatting

SENTENCE_ENDING_PUNCTUATION = ".!?"
CLOSING_PUNCTUATION_WRAPPERS = "\"'”’»›)]}」』"


def _is_horizontal_whitespace(char: str) -> bool:
    return char in " \t" or (char.isspace() and char not in "\n\r\v\f")


def remove_filler_words(text: str, filler_words: Iterable[str], enabled: bool) -> str:
    """Drop "um", "uh" and friends, ignoring the punctuation around them."""
    if not enabled:
        return text
    fillers = {word.lower() for word in filler_words}
    if not fillers:
        return text
    words = [word for word in text.split(" ") if word]
    kept = [
        word
        for word in words
        if word.lower().strip(string.punctuation + "…—–") not in fillers
    ]
    return " ".join(kept)


def apply_gaav_formatting(
    text: str, lowercase_first_letter: bool, remove_trailing_period: bool
) -> str:
    """Strip the leading capital and trailing period, for search boxes and chats."""
    if not text:
        return text
    result = text
    if remove_trailing_period and result.endswith("."):
        result = result[:-1]
    if lowercase_first_letter and result and result[0].isupper():
        result = result[0].lower() + result[1:]
    return result


def _last_capitalization_boundary_character(text: str) -> str | None:
    for char in reversed(text):
        if char in "\n\r":
            return None
        if _is_horizontal_whitespace(char) or char in CLOSING_PUNCTUATION_WRAPPERS:
            continue
        return char
    return None


def _replacing_first_letter(text: str, upper: bool) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            replacement = char.upper() if upper else char.lower()
            return text[:index] + replacement + text[index + 1 :]
    return text


def apply_continuous_dictation_formatting(
    text: str,
    preceding_text: str,
    spacing_enabled: bool,
    smart_caps_enabled: bool,
) -> str:
    """Make consecutive dictations read as one flowing document."""
    if not text:
        return text
    if not spacing_enabled and not smart_caps_enabled:
        return text

    result = text

    if smart_caps_enabled:
        boundary = _last_capitalization_boundary_character(preceding_text.strip(" \t"))
        starts_sentence = boundary is None or boundary in SENTENCE_ENDING_PUNCTUATION
        result = _replacing_first_letter(result, upper=starts_sentence)

    if spacing_enabled:
        if (
            preceding_text
            and not preceding_text[-1].isspace()
            and not (result and result[0].isspace())
        ):
            result = " " + result
        if not result or not result[-1].isspace():
            result += " "

    return result


@dataclass
class PipelineContext:
    """Everything the pipeline needs to know about where the text is going."""

    app_name: str | None = None
    bundle_id: str | None = None
    window_title: str | None = None
    preceding_text: str = ""


@dataclass
class PipelineResult:
    raw_text: str
    #: Text after cleanup but before any AI enhancement.
    cleaned_text: str
    #: The text that will actually be inserted.
    final_text: str
    should_send: bool = False


class TextPipeline:
    def __init__(self, settings: SettingsStore, dictionary: CustomDictionary | None = None) -> None:
        self.settings = settings
        self.dictionary = dictionary or CustomDictionary(settings.custom_dictionary_entries)

    def refresh_dictionary(self) -> None:
        self.dictionary.entries = self.settings.custom_dictionary_entries

    def clean(self, raw_text: str, context: PipelineContext | None = None) -> str:
        """Steps 1–3: the text an AI provider would be handed."""
        context = context or PipelineContext()
        settings = self.settings

        text = remove_filler_words(
            raw_text, settings.filler_words, settings.remove_filler_words_enabled
        )
        text = self.dictionary.apply(text)
        if settings.auto_convert_punctuation_enabled:
            text = apply_spoken_punctuation_formatting(
                text,
                prefix=settings.punctuation_dictionary_prefix,
                rules=settings.punctuation_dictionary_rules,
                action_rules=settings.spoken_formatting_action_rules,
                app_name=context.app_name,
                bundle_id=context.bundle_id,
                window_title=context.window_title,
            )
        return text

    def format_for_output(self, text: str, context: PipelineContext | None = None) -> str:
        """Steps 5–7: the transformations applied to the final text."""
        context = context or PipelineContext()
        settings = self.settings

        result = apply_dictation_literal_formatting(
            text,
            app_name=context.app_name,
            bundle_id=context.bundle_id,
            window_title=context.window_title,
            enabled=settings.literal_dictation_formatting_enabled,
        )
        result = apply_gaav_formatting(
            result,
            lowercase_first_letter=settings.gaav_lowercase_first_letter_enabled,
            remove_trailing_period=settings.gaav_remove_trailing_period_enabled,
        )
        result = apply_continuous_dictation_formatting(
            result,
            preceding_text=context.preceding_text,
            spacing_enabled=settings.continuous_dictation_spacing_enabled,
            smart_caps_enabled=settings.context_aware_capitalization_enabled,
        )
        return result

    def run(
        self,
        raw_text: str,
        context: PipelineContext | None = None,
        enhanced_text: str | None = None,
    ) -> PipelineResult:
        """The whole pipeline.

        `enhanced_text` is the AI provider's output when enhancement ran; the
        cleanup steps are never re-applied to it.
        """
        context = context or PipelineContext()
        cleaned = self.clean(raw_text, context)
        body = enhanced_text if enhanced_text is not None else cleaned

        should_send = False
        if self.settings.spoken_send_enabled:
            from .spoken_send import parse_spoken_send

            parsed = parse_spoken_send(
                body, phrase=self.settings.spoken_send_phrase, enabled=True
            )
            body = parsed.text
            should_send = parsed.should_send

        final_text = self.format_for_output(body, context)
        return PipelineResult(
            raw_text=raw_text,
            cleaned_text=cleaned,
            final_text=final_text,
            should_send=should_send,
        )

    def output_plan(self, text: str, context: PipelineContext | None = None):
        context = context or PipelineContext()
        return make_dictation_literal_output_plan(
            text,
            app_name=context.app_name,
            bundle_id=context.bundle_id,
            window_title=context.window_title,
            enabled=self.settings.literal_dictation_formatting_enabled,
        )
