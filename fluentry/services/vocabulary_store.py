"""JSON-backed store for ASR vocabulary boosting terms.

A port of `ParakeetVocabularyStore`, writing to `$XDG_DATA_HOME/fluentry`
instead of Application Support so the file survives updates and stays
user-editable.

Tuning parameters stay backend-controlled: the UI only manages the word list,
and stale aggressive values from an old file are never honoured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..persistence.defaults import data_home

DEFAULT_ALPHA = 2.8
DEFAULT_MIN_CTC_SCORE = -2.2
DEFAULT_MIN_SIMILARITY = 0.72
DEFAULT_MIN_COMBINED_CONFIDENCE = 0.64
DEFAULT_MIN_TERM_LENGTH = 3
MAX_TERMS = 256

VOCABULARY_FILE_NAME = "custom_vocabulary.json"


class VocabularyStoreError(Exception):
    pass


class InvalidVocabularyJSONError(VocabularyStoreError):
    def __init__(self, details: str) -> None:
        super().__init__(f"Invalid vocabulary JSON: {details}")
        self.details = details


@dataclass(frozen=True)
class VocabularyTerm:
    text: str
    weight: float | None = None
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": self.text}
        if self.weight is not None:
            payload["weight"] = self.weight
        if self.aliases:
            payload["aliases"] = list(self.aliases)
        return payload

    @staticmethod
    def from_dict(payload: Any) -> "VocabularyTerm":
        if isinstance(payload, str):
            return VocabularyTerm(text=payload)
        if not isinstance(payload, dict) or "text" not in payload:
            raise InvalidVocabularyJSONError("a term must be a string or an object with a text field")
        weight = payload.get("weight")
        return VocabularyTerm(
            text=str(payload["text"]),
            weight=float(weight) if isinstance(weight, (int, float)) else None,
            aliases=tuple(str(alias) for alias in (payload.get("aliases") or [])),
        )


@dataclass(frozen=True)
class ResolvedVocabularyConfig:
    alpha: float
    min_ctc_score: float
    min_similarity: float
    min_combined_confidence: float
    min_term_length: int
    terms: tuple[VocabularyTerm, ...]


def default_template() -> dict[str, Any]:
    return {
        "alpha": DEFAULT_ALPHA,
        "minCtcScore": DEFAULT_MIN_CTC_SCORE,
        "minSimilarity": DEFAULT_MIN_SIMILARITY,
        "minCombinedConfidence": DEFAULT_MIN_COMBINED_CONFIDENCE,
        "minTermLength": DEFAULT_MIN_TERM_LENGTH,
        "terms": [
            # Seeded with the app's own name, which speech models reliably
            # mishear, as a worked example of what boosting is for.
            {
                "text": "Fluentry",
                "aliases": ["fluent tree", "fluently", "fluentry"],
                "weight": 10.0,
            }
        ],
    }


def normalized_boost_terms(json_terms: Iterable[VocabularyTerm]) -> list[VocabularyTerm]:
    """Merge duplicate texts, dedupe aliases, and sort case-insensitively.

    Only terms explicitly added to custom-word boosting belong here. Instant
    replacements are deterministic post-ASR rules and must never become fuzzy
    ASR candidates.
    """
    merged: dict[str, VocabularyTerm] = {}

    def normalize_aliases(aliases: Iterable[str], excluding: str) -> tuple[str, ...]:
        normalized = [
            alias.strip()
            for alias in aliases
            if alias.strip() and alias.strip().lower() != excluding.lower()
        ]
        return tuple(sorted({alias.lower() for alias in normalized}))

    for term in json_terms:
        text = term.text.strip()
        if not text:
            continue
        key = text.lower()
        existing = merged.get(key)
        if existing is not None:
            combined_aliases = tuple(sorted(set(existing.aliases) | set(term.aliases)))
            combined_weight = max(existing.weight or 0, term.weight or 0)
            merged[key] = VocabularyTerm(
                text=existing.text,
                weight=combined_weight if combined_weight > 0 else None,
                aliases=combined_aliases,
            )
            continue
        merged[key] = VocabularyTerm(
            text=text,
            weight=term.weight,
            aliases=normalize_aliases(term.aliases, text),
        )

    return sorted(merged.values(), key=lambda term: term.text.casefold())


def normalize_user_terms(terms: Iterable[VocabularyTerm], max_terms: int = MAX_TERMS) -> list[VocabularyTerm]:
    seen: set[str] = set()
    normalized: list[VocabularyTerm] = []
    for term in terms:
        text = term.text.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        aliases = [
            alias.strip()
            for alias in term.aliases
            if alias.strip() and alias.strip().lower() != text.lower()
        ]
        normalized.append(
            VocabularyTerm(
                text=text,
                weight=term.weight,
                aliases=tuple(sorted({alias.lower() for alias in aliases})),
            )
        )
        if len(normalized) >= max_terms:
            break
    return normalized


class VocabularyStore:
    def __init__(self, path: Path | None = None, on_change: Callable[[], None] | None = None) -> None:
        self.path = path or (data_home() / VOCABULARY_FILE_NAME)
        self._on_change = on_change

    def ensure_file_exists(self) -> Path:
        if self.path.exists():
            return self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(default_template(), indent=2, sort_keys=True), encoding="utf-8")
        return self.path

    def load_raw_json(self) -> str:
        return self.ensure_file_exists().read_text(encoding="utf-8")

    def validate_json(self, raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise InvalidVocabularyJSONError(str(error)) from error
        if not isinstance(payload, dict):
            raise InvalidVocabularyJSONError("expected a JSON object")
        terms = payload.get("terms")
        if terms is not None and not isinstance(terms, list):
            raise InvalidVocabularyJSONError("terms must be a list")
        # Surfaces a malformed term before it reaches the ASR backend.
        for term in terms or []:
            VocabularyTerm.from_dict(term)
        return payload

    def save_raw_json(self, raw: str) -> None:
        self.validate_json(raw)
        self.ensure_file_exists().write_text(raw, encoding="utf-8")
        self._notify()

    def load_user_boost_terms(self) -> list[VocabularyTerm]:
        payload = self.validate_json(self.load_raw_json())
        terms = [VocabularyTerm.from_dict(item) for item in payload.get("terms") or []]
        return normalize_user_terms(terms)

    def save_user_boost_terms(self, terms: Sequence[VocabularyTerm]) -> None:
        normalized = normalize_user_terms(terms)
        config = {
            "alpha": DEFAULT_ALPHA,
            "minCtcScore": DEFAULT_MIN_CTC_SCORE,
            "minSimilarity": DEFAULT_MIN_SIMILARITY,
            "minCombinedConfidence": DEFAULT_MIN_COMBINED_CONFIDENCE,
            "minTermLength": DEFAULT_MIN_TERM_LENGTH,
            "terms": [term.to_dict() for term in normalized],
        }
        self.ensure_file_exists().write_text(
            json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
        )
        self._notify()

    def has_any_boost_terms(self) -> bool:
        try:
            return bool(self.load_resolved_config().terms)
        except VocabularyStoreError:
            return False

    def load_resolved_config(self) -> ResolvedVocabularyConfig:
        try:
            payload = self.validate_json(self.load_raw_json())
        except (VocabularyStoreError, OSError):
            payload = {"terms": []}
        terms = []
        for item in payload.get("terms") or []:
            try:
                terms.append(VocabularyTerm.from_dict(item))
            except InvalidVocabularyJSONError:
                continue
        return ResolvedVocabularyConfig(
            # Backend-tuned values, never read from the file: an old JSON can
            # otherwise pin stale, over-aggressive thresholds.
            alpha=DEFAULT_ALPHA,
            min_ctc_score=DEFAULT_MIN_CTC_SCORE,
            min_similarity=DEFAULT_MIN_SIMILARITY,
            min_combined_confidence=DEFAULT_MIN_COMBINED_CONFIDENCE,
            min_term_length=DEFAULT_MIN_TERM_LENGTH,
            terms=tuple(normalized_boost_terms(terms)),
        )

    def prioritized_terms(self, max_terms: int = MAX_TERMS) -> list[VocabularyTerm]:
        """Highest-weight terms first, so capping a big list keeps what matters."""
        resolved = self.load_resolved_config()
        ordered = sorted(
            resolved.terms, key=lambda term: (-(term.weight or 0), term.text.casefold())
        )
        return ordered[: min(max_terms, MAX_TERMS)]

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass
