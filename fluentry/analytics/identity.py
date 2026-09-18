"""The anonymous install identifier.

A port of `AnalyticsIdentityStore`. The id is random and generated locally;
it is never derived from anything about the machine or the person, so it
identifies an installation and nothing else. Deleting the file resets it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..persistence.defaults import data_home

IDENTITY_FILE_NAME = "install-id"


def identity_path() -> Path:
    return data_home() / IDENTITY_FILE_NAME


def distinct_id(path: Path | None = None) -> str:
    path = path or identity_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    identifier = str(uuid.uuid4()).lower()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(identifier, encoding="utf-8")
    except OSError:
        # An unwritable data directory must not break analytics-free operation.
        pass
    return identifier


def reset_distinct_id(path: Path | None = None) -> None:
    (path or identity_path()).unlink(missing_ok=True)


#: Stored alongside the other defaults, under the macOS key names, so a
#: restored backup keeps the same install age.
FIRST_OPEN_KEY = "AnalyticsFirstOpenAt"


def ensure_first_open_recorded(defaults) -> bool:
    """Stamp the first launch. True only the very first time."""
    if defaults.object(FIRST_OPEN_KEY) is not None:
        return False
    import time

    defaults.set(FIRST_OPEN_KEY, time.time())
    return True


def first_open_at(defaults) -> float | None:
    timestamp = defaults.double(FIRST_OPEN_KEY)
    return timestamp if timestamp > 0 else None
