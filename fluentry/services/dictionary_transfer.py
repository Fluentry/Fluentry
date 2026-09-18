"""Import and export the dictionary as a single JSON file.

A port of `DictionaryTransferService`. The decoder is deliberately generous:
files written by this app, by an older build, or returned by the local API all
decode, because the shapes differ only in key names:

* replacements under `replacements`, `items`, or `entries`,
* a replacement's triggers under `from` or `triggers`, as a list or a bare
  string; its text under `to` or `replacement`,
* custom words under `customWords`, `terms`, `items`, or `entries`, each a bare
  string or an object with `text`/`weight`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Sequence

from ..persistence.settings_types import CustomDictionaryEntry, now
from .vocabulary_store import VocabularyTerm

IMPORTED_CUSTOM_WORD_WEIGHT = 10.0
MAX_CUSTOM_WORDS = 256


class DictionaryTransferError(Exception):
    def __init__(self) -> None:
        super().__init__("The selected file is not a valid Fluentry dictionary file.")


class ImportMode(Enum):
    MERGE = "merge"
    REPLACE = "replace"


@dataclass(frozen=True)
class DictionaryTransferReplacement:
    from_triggers: tuple[str, ...]
    to: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": list(self.from_triggers), "to": self.to}

    @staticmethod
    def from_dict(payload: Any) -> "DictionaryTransferReplacement":
        if not isinstance(payload, dict):
            raise DictionaryTransferError()
        if "from" in payload:
            triggers = _string_list(payload["from"])
        elif "triggers" in payload:
            triggers = _string_list(payload["triggers"])
        else:
            raise DictionaryTransferError()

        if "to" in payload and payload["to"] is not None:
            to = payload["to"]
        elif "replacement" in payload:
            to = payload["replacement"]
        else:
            raise DictionaryTransferError()
        if not isinstance(to, str):
            raise DictionaryTransferError()
        return DictionaryTransferReplacement(tuple(triggers), to)


@dataclass(frozen=True)
class DictionaryTransferCustomWord:
    text: str
    weight: float | None = None

    def to_json(self) -> Any:
        if self.weight is None:
            return self.text
        return {"text": self.text, "weight": self.weight}

    @staticmethod
    def from_json(payload: Any) -> "DictionaryTransferCustomWord":
        if isinstance(payload, str):
            return DictionaryTransferCustomWord(payload, None)
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            weight = payload.get("weight")
            return DictionaryTransferCustomWord(
                payload["text"], float(weight) if isinstance(weight, (int, float)) else None
            )
        raise DictionaryTransferError()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    raise DictionaryTransferError()


@dataclass(frozen=True)
class DictionaryTransferDocument:
    replacements: tuple[DictionaryTransferReplacement, ...] = ()
    custom_words: tuple[DictionaryTransferCustomWord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "replacements": [item.to_dict() for item in self.replacements],
            "customWords": [item.to_json() for item in self.custom_words],
        }

    @staticmethod
    def from_dict(payload: Any) -> "DictionaryTransferDocument":
        if not isinstance(payload, dict):
            raise DictionaryTransferError()

        replacements, found_replacements = _decode_replacements(payload)
        custom_words, found_words = _decode_custom_words(payload)
        if not found_replacements and not found_words:
            raise DictionaryTransferError()
        return DictionaryTransferDocument(tuple(replacements), tuple(custom_words))


def _decode_replacements(payload: dict[str, Any]) -> tuple[list[DictionaryTransferReplacement], bool]:
    if "replacements" in payload:
        raw = payload["replacements"]
        if not isinstance(raw, list):
            raise DictionaryTransferError()
        return [DictionaryTransferReplacement.from_dict(item) for item in raw], True
    for key in ("items", "entries"):
        if key in payload and isinstance(payload[key], list):
            try:
                return [DictionaryTransferReplacement.from_dict(item) for item in payload[key]], True
            except DictionaryTransferError:
                continue
    return [], False


def _decode_custom_words(payload: dict[str, Any]) -> tuple[list[DictionaryTransferCustomWord], bool]:
    for key in ("customWords", "terms"):
        if key in payload:
            raw = payload[key]
            if not isinstance(raw, list):
                raise DictionaryTransferError()
            return [DictionaryTransferCustomWord.from_json(item) for item in raw], True
    for key in ("items", "entries"):
        if key in payload and isinstance(payload[key], list):
            try:
                return [DictionaryTransferCustomWord.from_json(item) for item in payload[key]], True
            except DictionaryTransferError:
                continue
    return [], False


@dataclass(frozen=True)
class DictionaryTransferState:
    replacements: tuple[CustomDictionaryEntry, ...]
    custom_words: tuple[VocabularyTerm, ...]


@dataclass(frozen=True)
class DictionaryTransferSummary:
    replacement_count: int
    custom_word_count: int


def normalized_unique_strings(values: Iterable[str], lowercased: bool) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        trimmed = value.strip()
        if not trimmed:
            continue
        output = trimmed.lower() if lowercased else trimmed
        key = output.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(output)
    return result


def _export_replacement(
    triggers: Iterable[str], replacement: str
) -> DictionaryTransferReplacement | None:
    from_triggers = normalized_unique_strings(triggers, lowercased=True)
    to = CustomDictionaryEntry.sanitized_replacement(replacement)
    if not from_triggers or not to:
        return None
    return DictionaryTransferReplacement(tuple(from_triggers), to)


def _export_custom_words(
    words: Iterable[DictionaryTransferCustomWord],
) -> list[DictionaryTransferCustomWord]:
    seen: set[str] = set()
    result: list[DictionaryTransferCustomWord] = []
    for word in words:
        text = word.text.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(DictionaryTransferCustomWord(text, word.weight))
    return result


def normalized_document(document: DictionaryTransferDocument) -> DictionaryTransferDocument:
    replacements = [
        exported
        for exported in (
            _export_replacement(item.from_triggers, item.to) for item in document.replacements
        )
        if exported is not None
    ]
    return DictionaryTransferDocument(
        tuple(replacements), tuple(_export_custom_words(document.custom_words))
    )


def _upsert(entry: CustomDictionaryEntry, entries: list[CustomDictionaryEntry]) -> None:
    """Fold an imported entry in, moving duplicate triggers off other entries."""
    matching_index = next(
        (
            index
            for index, existing in enumerate(entries)
            if existing.replacement.casefold() == entry.replacement.casefold()
        ),
        None,
    )
    replacement_id = entries[matching_index].id if matching_index is not None else entry.id
    replacement_text = (
        entries[matching_index].replacement if matching_index is not None else entry.replacement
    )
    previous_triggers = entries[matching_index].triggers if matching_index is not None else []
    combined_triggers = normalized_unique_strings(
        list(previous_triggers) + list(entry.triggers), lowercased=True
    )
    trigger_keys = {trigger.lower() for trigger in combined_triggers}

    entries[:] = [
        existing
        for existing in entries
        if existing.replacement.casefold() != entry.replacement.casefold()
    ]

    surviving: list[CustomDictionaryEntry] = []
    for existing in entries:
        remaining = [
            trigger for trigger in existing.triggers if trigger.lower() not in trigger_keys
        ]
        if not remaining:
            continue
        surviving.append(
            CustomDictionaryEntry(id=existing.id, triggers=remaining, replacement=existing.replacement)
        )
    entries[:] = surviving
    entries.append(
        CustomDictionaryEntry(
            id=replacement_id, triggers=combined_triggers, replacement=replacement_text
        )
    )


def import_state(
    document: DictionaryTransferDocument,
    mode: ImportMode,
    current_replacements: Sequence[CustomDictionaryEntry] = (),
    current_custom_words: Sequence[VocabularyTerm] = (),
) -> DictionaryTransferState:
    normalized = normalized_document(document)

    replacements: list[CustomDictionaryEntry] = (
        [] if mode is ImportMode.REPLACE else list(current_replacements)
    )
    for replacement in normalized.replacements:
        triggers = normalized_unique_strings(replacement.from_triggers, lowercased=True)
        to = CustomDictionaryEntry.sanitized_replacement(replacement.to)
        if not triggers or not to:
            continue
        _upsert(CustomDictionaryEntry(triggers=triggers, replacement=to), replacements)

    custom_words: list[VocabularyTerm] = (
        [] if mode is ImportMode.REPLACE else list(current_custom_words)
    )
    for word in normalized.custom_words:
        if len(custom_words) >= MAX_CUSTOM_WORDS:
            break
        if any(term.text.casefold() == word.text.casefold() for term in custom_words):
            continue
        custom_words.append(
            VocabularyTerm(
                text=word.text,
                weight=word.weight if word.weight is not None else IMPORTED_CUSTOM_WORD_WEIGHT,
                aliases=(),
            )
        )

    return DictionaryTransferState(tuple(replacements), tuple(custom_words))


class DictionaryTransferService:
    def __init__(self, settings=None, vocabulary_store=None, dictionary=None) -> None:
        self.settings = settings
        self.vocabulary_store = vocabulary_store
        self.dictionary = dictionary

    def make_export_document(self) -> DictionaryTransferDocument:
        replacements = []
        if self.settings is not None:
            for entry in self.settings.custom_dictionary_entries:
                exported = _export_replacement(entry.triggers, entry.replacement)
                if exported is not None:
                    replacements.append(exported)
        custom_words: list[DictionaryTransferCustomWord] = []
        if self.vocabulary_store is not None:
            custom_words = [
                DictionaryTransferCustomWord(term.text, term.weight)
                for term in self.vocabulary_store.load_user_boost_terms()
            ]
        return DictionaryTransferDocument(
            tuple(replacements), tuple(_export_custom_words(custom_words))
        )

    def encode(self, document: DictionaryTransferDocument) -> bytes:
        return json.dumps(
            normalized_document(document).to_dict(), indent=2, sort_keys=True
        ).encode("utf-8")

    def decode(self, data: bytes | str) -> DictionaryTransferDocument:
        raw = data.decode("utf-8") if isinstance(data, bytes) else data
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise DictionaryTransferError() from error
        return normalized_document(DictionaryTransferDocument.from_dict(payload))

    def restore(
        self, document: DictionaryTransferDocument, mode: ImportMode
    ) -> DictionaryTransferSummary:
        current_replacements = self.settings.custom_dictionary_entries if self.settings else []
        current_words = (
            self.vocabulary_store.load_user_boost_terms() if self.vocabulary_store else []
        )
        state = import_state(document, mode, current_replacements, current_words)

        if self.vocabulary_store is not None:
            self.vocabulary_store.save_user_boost_terms(state.custom_words)
        if self.settings is not None:
            self.settings.custom_dictionary_entries = list(state.replacements)
        if self.dictionary is not None:
            self.dictionary.entries = list(state.replacements)

        return DictionaryTransferSummary(
            replacement_count=len(state.replacements), custom_word_count=len(state.custom_words)
        )

    def suggested_filename(self, moment: datetime | None = None) -> str:
        moment = moment or now()
        return f"Fluentry_Dictionary_{moment.strftime('%Y-%m-%d_%H-%M')}.json"
