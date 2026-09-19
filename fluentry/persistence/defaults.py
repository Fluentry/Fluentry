"""`UserDefaults` equivalent for Linux.

A typed, lazily-persisted preferences domain. The closest
Linux convention is a JSON file under `$XDG_CONFIG_HOME`, so that is what backs
the real store. The typed accessor semantics are copied exactly, because a
surprising amount of the app's behaviour depends on them:

* `object(key)` returns `None` when a key was never written — the difference
  between "unset" (use the default) and "explicitly false" that several
  settings rely on,
* `bool`/`integer`/`double` coerce a missing key to `False`/`0`/`0.0`,
* `string_array` returns `None` rather than `[]` when unset.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable


#: What the app was called before it was renamed. An install from a build
#: that predates the rename keeps its settings, history and models under
#: this name, so the directories are moved across on first use.
LEGACY_APP_DIRECTORY = "fluidvoice"
APP_DIRECTORY = "fluentry"


def _app_directory(base: Path) -> Path:
    """The app's directory under an XDG base, migrating the old name once."""
    current = base / APP_DIRECTORY
    legacy = base / LEGACY_APP_DIRECTORY
    if not current.exists() and legacy.is_dir():
        try:
            legacy.rename(current)
        except OSError:
            # A failed move must not lose the old data or crash the app: fall
            # back to a fresh directory and leave the original untouched.
            return current
    return current


def config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    base = Path(raw) if raw else Path.home() / ".config"
    return _app_directory(base)


def data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw) if raw else Path.home() / ".local" / "share"
    return _app_directory(base)


def cache_home() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    base = Path(raw) if raw else Path.home() / ".cache"
    return _app_directory(base)


def state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    base = Path(raw) if raw else Path.home() / ".local" / "state"
    return _app_directory(base)


class Defaults:
    """In-memory preferences domain. `FileDefaults` adds persistence."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = dict(initial or {})
        self._lock = threading.RLock()

    # --- reads ------------------------------------------------------------

    def object(self, key: str) -> Any | None:
        with self._lock:
            return self._values.get(key)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._values

    def bool(self, key: str) -> bool:
        value = self.object(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            # UserDefaults coerces "true"/"YES"/"1" strings the same way.
            return value.strip().lower() in {"true", "yes", "1"}
        return False

    def integer(self, key: str) -> int:
        value = self.object(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                return 0
        return 0

    def double(self, key: str) -> float:
        value = self.object(key)
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def string(self, key: str) -> str | None:
        value = self.object(key)
        return value if isinstance(value, str) else None

    def string_array(self, key: str) -> list[str] | None:
        value = self.object(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
        return None

    def array(self, key: str) -> list | None:
        value = self.object(key)
        return list(value) if isinstance(value, list) else None

    def dictionary(self, key: str) -> dict | None:
        value = self.object(key)
        return dict(value) if isinstance(value, dict) else None

    def json(self, key: str) -> Any | None:
        """Decode a value stored as a JSON payload.

        Structured values are stored as
        native JSON, but a string payload is still accepted so files written by
        an older build keep loading.
        """
        value = self.object(key)
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return None
        return value

    # --- writes -----------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        if value is None:
            self.remove(key)
            return
        with self._lock:
            self._values[key] = value
        self._did_change()

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, value)

    def remove(self, key: str) -> None:
        with self._lock:
            existed = self._values.pop(key, _MISSING) is not _MISSING
        if existed:
            self._did_change()

    def remove_all(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.remove(key)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._values))

    def _did_change(self) -> None:  # pragma: no cover - overridden by subclass
        pass


class _Missing:
    pass


_MISSING = _Missing()


class FileDefaults(Defaults):
    """Preferences persisted to `~/.config/fluentry/settings.json`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_home() / "settings.json")
        super().__init__(self._load())
        self._dirty = False

    def _load(self) -> dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            return {}

    def _did_change(self) -> None:
        self.synchronize()

    def synchronize(self) -> None:
        """Atomically rewrite the backing file."""
        snapshot = self.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            # A read-only or full home directory must never crash dictation.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
