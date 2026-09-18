"""Presentation helpers for the History screen.

Ports `HistoryTextDiff` and `HistoryAudioAvailability`. Both are deliberately
bounded: a history row must never block on a long diff or on disk access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Iterable, Sequence

#: Comparing two very long transcripts is not worth a stalled row.
MAXIMUM_DIFF_BYTES = 24_000
MAXIMUM_DIFF_TOKENS = 2_048

#: Whitespace runs, word runs, then single punctuation/symbol characters. The
#: order matters: whitespace is captured verbatim so it is never "changed".
TOKEN_PATTERN = re.compile(r"\s+|\w+|[^\s\w]", re.UNICODE)


@dataclass
class DiffRun:
    text: str
    changed: bool

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DiffRun):
            return self.text == other.text and self.changed == other.changed
        return NotImplemented


@dataclass(frozen=True)
class HistoryTextDiff:
    original: tuple[DiffRun, ...]
    final: tuple[DiffRun, ...]

    @property
    def has_changes(self) -> bool:
        return any(run.changed for run in self.original) or any(run.changed for run in self.final)

    @staticmethod
    def compare(original: str, final: str) -> "HistoryTextDiff | None":
        """Word-level diff, or None when the inputs are too large to be worth it."""
        if len(original.encode("utf-8")) + len(final.encode("utf-8")) > MAXIMUM_DIFF_BYTES:
            return None

        before = TOKEN_PATTERN.findall(original)
        after = TOKEN_PATTERN.findall(final)
        if len(before) + len(after) > MAXIMUM_DIFF_TOKENS:
            return None

        removed: set[int] = set()
        added: set[int] = set()
        matcher = SequenceMatcher(a=before, b=after, autojunk=False)
        for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            removed.update(range(before_start, before_end))
            added.update(range(after_start, after_end))

        return HistoryTextDiff(
            original=tuple(_runs(before, removed)),
            final=tuple(_runs(after, added)),
        )


def _runs(tokens: Sequence[str], changed: set[int]) -> list[DiffRun]:
    """Collapse adjacent tokens with the same changed-ness into one run."""
    result: list[DiffRun] = []
    for index, token in enumerate(tokens):
        is_changed = index in changed
        if result and result[-1].changed == is_changed:
            result[-1].text += token
        else:
            result.append(DiffRun(text=token, changed=is_changed))
    return result


def scan_audio_availability(
    file_names: Iterable[str], exists: Callable[[str], bool]
) -> set[str]:
    """Which saved recordings are still on disk.

    Taken once per view lifetime so row rendering never touches the disk, and
    it never writes anything back.
    """
    available: set[str] = set()
    for file_name in set(file_names):
        if exists(file_name):
            available.add(file_name)
    return available
