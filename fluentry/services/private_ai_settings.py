"""Browsing, choosing and updating the Private AI model.

A port of `PrivateAIModelRecommendation`, `PrivateAIModelCarouselNavigation`,
`PrivateAISettingsSession` and `PrivateAISettingsUpdateTransaction`.

Two rules run through all of it:

* **browsing is not choosing.** Flicking through the model carousel must
  never start a download or switch the active model; only an explicit
  activation does.
* **one mutation at a time, and the slot stays held until the rollback has
  finished** — not merely until verification did. Releasing it early lets a
  second download start while the first is still undoing itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

GIGABYTE = 1024 * 1024 * 1024

#: The two models this has ever offered; kept so a stored choice resolves.
MINI_MODEL_ID = "fluid-1-mini-96k-dflash"
PICO_MODEL_ID = "fluid-1-pico-96k-dflash"

#: Below this, the smaller model is the honest recommendation.
RECOMMENDATION_MEMORY_THRESHOLD = 16 * GIGABYTE


def recommended_model_id(physical_memory_bytes: int) -> str:
    return MINI_MODEL_ID if physical_memory_bytes >= RECOMMENDATION_MEMORY_THRESHOLD else PICO_MODEL_ID


def carousel_next(ids: Sequence[str], current: str, forward: bool) -> str | None:
    """The neighbouring model, wrapping around.

    An empty catalog has no target, and a single-model catalog never invents
    a neighbour — it returns the one model it has.
    """
    if not ids:
        return None
    try:
        index = list(ids).index(current)
    except ValueError:
        # A stale preview (a model that was deleted) recovers to a real one.
        return ids[0]
    step = 1 if forward else len(ids) - 1
    return ids[(index + step) % len(ids)]


def carousel_position(identifier: str, ids: Sequence[str], current: str) -> int:
    """Where a card sits relative to the centred one: negative is left."""
    ids = list(ids)
    if not ids or identifier not in ids:
        return 0
    index = ids.index(identifier)
    center = ids.index(current) if current in ids else 0
    if len(ids) == 2:
        return index - center
    distance = (index - center + len(ids)) % len(ids)
    return distance - len(ids) if distance > len(ids) // 2 else distance


@dataclass
class PrivateAISettingsSession:
    """Presentation-only choice plus a single bounded mutation slot."""

    selected_model_id: str
    preview_model_id: str = ""
    revision: str = field(default_factory=lambda: str(uuid.uuid4()))
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.preview_model_id:
            self.preview_model_id = self.selected_model_id

    @property
    def is_busy(self) -> bool:
        return self.operation_id is not None

    def preview(self, model_id: str) -> None:
        """Browsing. Deliberately does not touch selection or revision."""
        self.preview_model_id = model_id

    def select(self, model_id: str) -> bool:
        """Explicit activation. The caller validates the id against the catalog."""
        if self.is_busy:
            return False
        self.selected_model_id = model_id
        self.preview_model_id = model_id
        self.revision = str(uuid.uuid4())
        return True

    def begin(self) -> str | None:
        if self.is_busy:
            return None
        self.operation_id = token = str(uuid.uuid4())
        self.revision = str(uuid.uuid4())
        return token

    def finish(self, token: str) -> None:
        # A late completion from an earlier operation must not free the slot.
        if self.operation_id != token:
            return
        self.operation_id = None
        self.revision = str(uuid.uuid4())

    def accepts_read(self, revision: str) -> bool:
        """Whether a status read taken at `revision` is still current."""
        return not self.is_busy and self.revision == revision


class OperationCancelled(Exception):
    """Raised when an update was cancelled partway through."""


Token = TypeVar("Token")


def run_update_transaction(
    update: Callable[[], Token],
    verify: Callable[[], bool],
    commit: Callable[[Token], None],
    rollback: Callable[[Token], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> bool:
    """Stage, verify, then either commit or put the old artifact back.

    A failure inside `update` never rolls back, because there is no token to
    roll back yet — nothing was staged.
    """
    token = update()
    try:
        if is_cancelled():
            raise OperationCancelled()
        verified = verify()
        if is_cancelled():
            raise OperationCancelled()
        if verified:
            commit(token)
        else:
            rollback(token)
        return verified
    except BaseException:
        rollback(token)
        raise
