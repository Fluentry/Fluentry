"""Learn dictionary entries from the corrections a user types by hand.

When a dictation lands and the user immediately fixes a word inside the text
that was just inserted, that edit is a strong signal the model misheard them.
`AutomaticDictionaryCorrectionDetector` decides whether an edit is such a
correction; `AutomaticDictionarySuggestionPolicy` decides whether it is worth
interrupting the user to offer a dictionary entry.

A port of `AutomaticDictionaryCorrectionTracker.swift`. Ranges are
`TextRange(location, length)` over Python string indices — the macOS original
used UTF-16 offsets for the same purpose.
"""

from __future__ import annotations

import json
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from ..persistence.defaults import Defaults
from ..persistence.settings_types import iso, now as utc_now, parse_iso

EDGE_CHARACTERS = set(" \t\n\r\v\f") | set(".,!?;:\"“”‘’()[]{}")
BOUNDARY_CHARACTERS = set(" \t\n\r\v\f") | set(",!?;:\"“”()[]{}<>")

MAX_CANDIDATE_LENGTH = 40
MAX_COMBINED_LENGTH = 70
MAX_WORDS = 3


@dataclass(frozen=True)
class TextRange:
    location: int
    length: int

    @property
    def end(self) -> int:
        return self.location + self.length

    def intersects(self, other: "TextRange") -> bool:
        return max(self.location, other.location) < min(self.end, other.end)


NOT_FOUND = -1


@dataclass(frozen=True)
class AutomaticDictionaryCorrectionCandidate:
    heard_text: str
    corrected_text: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper(), compare=False)


@dataclass(frozen=True)
class AutomaticDictionaryTextChange:
    old_range: TextRange
    new_range: TextRange


def text_change(before: str, after: str) -> AutomaticDictionaryTextChange | None:
    """The minimal differing span, found by trimming the shared prefix/suffix."""
    if before == after:
        return None

    shared_length = min(len(before), len(after))
    prefix_length = 0
    while prefix_length < shared_length and before[prefix_length] == after[prefix_length]:
        prefix_length += 1

    suffix_length = 0
    old_remaining = len(before) - prefix_length
    new_remaining = len(after) - prefix_length
    while (
        suffix_length < min(old_remaining, new_remaining)
        and before[len(before) - suffix_length - 1] == after[len(after) - suffix_length - 1]
    ):
        suffix_length += 1

    return AutomaticDictionaryTextChange(
        old_range=TextRange(prefix_length, len(before) - prefix_length - suffix_length),
        new_range=TextRange(prefix_length, len(after) - prefix_length - suffix_length),
    )


def is_change_inside_inserted_range(
    change: AutomaticDictionaryTextChange,
    inserted_range: TextRange,
    allows_insertion_at_end: bool = False,
) -> bool:
    if inserted_range.location == NOT_FOUND or inserted_range.length <= 0:
        return False
    inserted_end = inserted_range.end

    if change.old_range.length == 0:
        return change.old_range.location >= inserted_range.location and (
            change.old_range.location < inserted_end
            or (allows_insertion_at_end and change.old_range.location == inserted_end)
        )

    return (
        change.old_range.location >= inserted_range.location
        and change.old_range.end <= inserted_end
    )


def _expanded_token_range(text: str, span: TextRange) -> TextRange:
    safe_location = max(0, min(span.location, len(text)))
    safe_end = max(safe_location, min(span.end, len(text)))
    start = safe_location
    end = safe_end
    while start > 0 and text[start - 1] not in BOUNDARY_CHARACTERS:
        start -= 1
    while end < len(text) and text[end] not in BOUNDARY_CHARACTERS:
        end += 1
    return TextRange(start, end - start)


def _cleaned_candidate(value: str) -> str:
    return value.strip("".join(EDGE_CHARACTERS))


def _is_valid_candidate(value: str) -> bool:
    if not value or len(value) > MAX_CANDIDATE_LENGTH:
        return False
    if not any(char.isalnum() for char in value):
        return False
    words = value.split()
    return bool(words) and len(words) <= MAX_WORDS


def _is_meaningful_correction(heard: str, corrected: str) -> bool:
    """Ignore edits that only changed punctuation or spacing."""
    heard_characters = [char for char in heard if char.isalnum()]
    corrected_characters = [char for char in corrected if char.isalnum()]
    if len(heard_characters) < 2 or len(corrected_characters) < 2:
        return False
    if not any(char.isalpha() for char in heard_characters):
        return False
    if not any(char.isalpha() for char in corrected_characters):
        return False
    return "".join(heard_characters) != "".join(corrected_characters)


def correction_candidate(
    before: str,
    after: str,
    inserted_range: TextRange,
    allows_insertion_at_end: bool = False,
) -> AutomaticDictionaryCorrectionCandidate | None:
    change = text_change(before, after)
    if change is None:
        return None
    if not is_change_inside_inserted_range(change, inserted_range, allows_insertion_at_end):
        return None

    old_token_range = _expanded_token_range(before, change.old_range)
    new_token_range = _expanded_token_range(after, change.new_range)
    if old_token_range.location < inserted_range.location or old_token_range.end > inserted_range.end:
        return None

    heard = _cleaned_candidate(before[old_token_range.location : old_token_range.end])
    corrected = _cleaned_candidate(after[new_token_range.location : new_token_range.end])

    if not _is_valid_candidate(heard) or not _is_valid_candidate(corrected):
        return None
    if not _is_meaningful_correction(heard, corrected):
        return None
    if heard == corrected:
        return None
    if len(heard) + len(corrected) > MAX_COMBINED_LENGTH:
        return None

    return AutomaticDictionaryCorrectionCandidate(heard_text=heard, corrected_text=corrected)


def is_word_continuation_at_inserted_range_end(
    change: AutomaticDictionaryTextChange, after: str, inserted_range: TextRange
) -> bool:
    """True when the user is still typing the word that ends the dictation."""
    if change.old_range.length != 0:
        return False
    if change.old_range.location != inserted_range.end:
        return False
    if change.new_range.end > len(after):
        return False
    inserted_text = after[change.new_range.location : change.new_range.end]
    return bool(inserted_text) and all(char not in BOUNDARY_CHARACTERS for char in inserted_text)


def corrected_token_range(before: str, after: str) -> TextRange | None:
    change = text_change(before, after)
    if change is None:
        return None
    return _expanded_token_range(after, change.new_range)


def selection_touches_candidate(selection: TextRange, candidate_range: TextRange) -> bool:
    if selection.location == NOT_FOUND or candidate_range.location == NOT_FOUND:
        return False
    if selection.length == 0:
        return candidate_range.location <= selection.location <= candidate_range.end
    return selection.intersects(candidate_range)


def change_continues_candidate(
    change: AutomaticDictionaryTextChange, after: str, candidate_range: TextRange
) -> bool:
    if change.old_range.length > 0:
        return change.old_range.intersects(candidate_range)

    if not (candidate_range.location <= change.old_range.location <= candidate_range.end):
        return False
    if change.old_range.location != candidate_range.end:
        return True
    if change.new_range.location == NOT_FOUND or change.new_range.end > len(after):
        return False
    inserted_text = after[change.new_range.location : change.new_range.end]
    return all(char not in BOUNDARY_CHARACTERS for char in inserted_text)


# --- suggestion policy ------------------------------------------------------

SUGGESTION_POLICY_DEFAULTS_KEY = "AutomaticDictionarySuggestionPolicyStateV1"


@dataclass
class DictionarySuggestionPolicyConfig:
    required_occurrences: int = 2
    occurrence_window: timedelta = timedelta(days=7)
    dismissed_pair_cooldown: timedelta = timedelta(days=7)
    maximum_session_ignores: int = 3
    retention_duration: timedelta = timedelta(days=30)
    maximum_stored_pairs: int = 200


class SuggestionOutcome:
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    DISMISSED = "dismissed"
    TIMED_OUT = "timedOut"


@dataclass
class _PairRecord:
    heard_text: str
    corrected_text: str
    occurrences: list[datetime] = field(default_factory=list)
    last_shown_at: datetime | None = None
    dismissed_until: datetime | None = None
    is_accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "heardText": self.heard_text,
            "correctedText": self.corrected_text,
            "occurrences": [iso(moment) for moment in self.occurrences],
            "lastShownAt": iso(self.last_shown_at) if self.last_shown_at else None,
            "dismissedUntil": iso(self.dismissed_until) if self.dismissed_until else None,
            "isAccepted": self.is_accepted,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "_PairRecord":
        return _PairRecord(
            heard_text=str(payload.get("heardText") or ""),
            corrected_text=str(payload.get("correctedText") or ""),
            occurrences=[parse_iso(item) for item in payload.get("occurrences") or []],
            last_shown_at=parse_iso(payload["lastShownAt"]) if payload.get("lastShownAt") else None,
            dismissed_until=parse_iso(payload["dismissedUntil"]) if payload.get("dismissedUntil") else None,
            is_accepted=bool(payload.get("isAccepted") or False),
        )


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.strip())
    without_marks = "".join(char for char in folded if not unicodedata.combining(char))
    return without_marks.lower()


class AutomaticDictionarySuggestionPolicy:
    """Decides when a repeated correction is worth suggesting."""

    def __init__(
        self,
        defaults: Defaults | None = None,
        configuration: DictionarySuggestionPolicyConfig | None = None,
    ) -> None:
        self.defaults = defaults if defaults is not None else Defaults()
        self.configuration = configuration or DictionarySuggestionPolicyConfig()
        self._records: dict[str, _PairRecord] = {}
        self.session_ignore_count = 0
        self._load()

    def _load(self) -> None:
        payload = self.defaults.json(SUGGESTION_POLICY_DEFAULTS_KEY)
        if not isinstance(payload, dict):
            return
        records = payload.get("records")
        if not isinstance(records, dict):
            return
        for key, value in records.items():
            if isinstance(value, dict):
                self._records[key] = _PairRecord.from_dict(value)

    def _save(self) -> None:
        self.defaults.set(
            SUGGESTION_POLICY_DEFAULTS_KEY,
            {"records": {key: record.to_dict() for key, record in self._records.items()}},
        )

    @staticmethod
    def key(candidate: AutomaticDictionaryCorrectionCandidate) -> str:
        return f"{_normalized(candidate.heard_text)}{_normalized(candidate.corrected_text)}"

    def should_show(
        self,
        candidate: AutomaticDictionaryCorrectionCandidate,
        required_occurrences: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        now = now or utc_now()
        self._prune(now)
        key = self.key(candidate)
        record = self._records.get(key) or _PairRecord(
            heard_text=candidate.heard_text, corrected_text=candidate.corrected_text
        )
        record.occurrences = [
            moment
            for moment in record.occurrences
            if now - moment <= self.configuration.occurrence_window
        ]
        record.occurrences.append(now)
        self._records[key] = record
        self._save()

        corrected_text = _normalized(candidate.corrected_text)
        # Different mishearings of the same word all count toward the threshold.
        occurrence_count = sum(
            1
            for stored in self._records.values()
            if _normalized(stored.corrected_text) == corrected_text
            for moment in stored.occurrences
            if now - moment <= self.configuration.occurrence_window
        )

        if record.is_accepted:
            return False
        if record.dismissed_until is not None and record.dismissed_until > now:
            return False
        if occurrence_count < (required_occurrences or self.configuration.required_occurrences):
            return False
        if self.session_ignore_count >= self.configuration.maximum_session_ignores:
            return False
        return True

    def mark_shown(
        self, candidate: AutomaticDictionaryCorrectionCandidate, now: datetime | None = None
    ) -> None:
        key = self.key(candidate)
        record = self._records.get(key)
        if record is None:
            return
        record.last_shown_at = now or utc_now()
        self._save()

    def record(
        self,
        outcome: str,
        candidate: AutomaticDictionaryCorrectionCandidate,
        now: datetime | None = None,
    ) -> None:
        now = now or utc_now()
        key = self.key(candidate)
        record = self._records.get(key)
        if record is None:
            return
        if outcome in (SuggestionOutcome.ACCEPTED, SuggestionOutcome.IGNORED):
            # Both mean "stop asking about this pair" — accepted adds the entry,
            # ignored means the user resolved it themselves.
            record.is_accepted = True
            record.dismissed_until = None
        else:
            record.dismissed_until = now + self.configuration.dismissed_pair_cooldown
            self.session_ignore_count += 1
        self._save()

    def _prune(self, now: datetime) -> None:
        retained: dict[str, _PairRecord] = {}
        for key, record in self._records.items():
            if record.is_accepted:
                retained[key] = record
                continue
            activity = [moment for moment in record.occurrences]
            if record.last_shown_at is not None:
                activity.append(record.last_shown_at)
            if activity and (now - max(activity)) <= self.configuration.retention_duration:
                retained[key] = record
        if len(retained) > self.configuration.maximum_stored_pairs:
            ordered = sorted(
                retained.items(),
                key=lambda item: item[1].last_shown_at or datetime.min.replace(tzinfo=now.tzinfo),
                reverse=True,
            )
            retained = dict(ordered[: self.configuration.maximum_stored_pairs])
        self._records = retained
