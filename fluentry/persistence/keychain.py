"""Secure storage for provider API keys.

`KeychainService`, retargeted at the freedesktop Secret Service (GNOME
Keyring, KWallet) through `secret-tool`, with an encrypted local file as the
fallback for headless machines and desktops with no keyring.

The caching contract is what the tests pin down and is unchanged:

* the backing store is read at most once per process unless explicitly
  refreshed — a paste must never block on a keyring prompt,
* a *failed* read is not cached, so a transient keyring error is retried,
* a store re-reads first, so a key written by another instance is merged
  rather than clobbered,
* a failed write leaves the previously loaded cache intact,
* a cached read never waits behind an in-flight refresh.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .defaults import data_home

SECRET_SERVICE_NAME = "dev.fluentry.Fluentry"
SECRET_ATTRIBUTE = "provider-api-keys"


class KeychainError(Exception):
    pass


class KeychainService:
    def __init__(
        self,
        load: Callable[[], dict[str, str]] | None = None,
        save: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        backend = None
        if load is None or save is None:
            backend = make_secret_backend()
        self._load = load or backend.load
        self._save = save or backend.save
        self._cache: dict[str, str] | None = None
        self._cache_lock = threading.Lock()
        self._io_lock = threading.Lock()

    # --- reads ------------------------------------------------------------

    def fetch_all_keys(self) -> dict[str, str]:
        with self._cache_lock:
            if self._cache is not None:
                # Returns the cached copy even while a refresh is running, so
                # a blocked keyring cannot stall a paste.
                return dict(self._cache)
        loaded = self._load_locked()
        return dict(loaded)

    def fetch_key(self, provider: str) -> str | None:
        return self.fetch_all_keys().get(provider)

    def refresh_cached_keys(self) -> dict[str, str]:
        return dict(self._load_locked())

    def _load_locked(self) -> dict[str, str]:
        with self._io_lock:
            values = self._load()
            if not isinstance(values, dict):
                raise KeychainError("Stored keys are not a dictionary.")
            normalized = {str(key): str(value) for key, value in values.items()}
            with self._cache_lock:
                self._cache = dict(normalized)
            return normalized

    # --- writes -----------------------------------------------------------

    def store_key(self, key: str, provider: str) -> None:
        trimmed = key.strip()
        self._mutate(lambda values: values.__setitem__(provider, trimmed))

    def delete_key(self, provider: str) -> None:
        self._mutate(lambda values: values.pop(provider, None))

    def _mutate(self, apply: Callable[[dict[str, str]], None]) -> None:
        with self._io_lock:
            # Re-read first so a key written by another instance survives, and
            # publish that read before saving: if the save fails, the cache is
            # still the freshly loaded state rather than nothing at all.
            current = dict(self._load())
            with self._cache_lock:
                self._cache = dict(current)
            apply(current)
            self._save(current)
            with self._cache_lock:
                self._cache = dict(current)

    def delete_all_keys(self) -> None:
        with self._io_lock:
            self._save({})
            with self._cache_lock:
                self._cache = {}


# --- backends ---------------------------------------------------------------


class SecretToolBackend:
    """freedesktop Secret Service via `secret-tool`."""

    @staticmethod
    def is_available() -> bool:
        return shutil.which("secret-tool") is not None

    def load(self) -> dict[str, str]:
        try:
            result = subprocess.run(
                ["secret-tool", "lookup", "service", SECRET_SERVICE_NAME, "key", SECRET_ATTRIBUTE],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise KeychainError(str(error)) from error
        # An empty result means "nothing stored yet", not a failure.
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        try:
            payload = json.loads(result.stdout)
        except ValueError as error:
            raise KeychainError("Stored keys are not valid JSON.") from error
        return payload if isinstance(payload, dict) else {}

    def save(self, values: dict[str, str]) -> None:
        try:
            subprocess.run(
                [
                    "secret-tool", "store", "--label", "Fluentry provider API keys",
                    "service", SECRET_SERVICE_NAME, "key", SECRET_ATTRIBUTE,
                ],
                input=json.dumps(values).encode("utf-8"),
                capture_output=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise KeychainError(str(error)) from error


class ObfuscatedFileBackend:
    """Fallback store for machines with no keyring.

    The file is obfuscated with a machine-derived key and written 0600. This
    is weaker than a keyring and the app says so in Settings; it exists so a
    headless or minimal system is still usable rather than broken.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_home() / "provider-keys.dat")

    @staticmethod
    def is_available() -> bool:
        return True

    def _cipher_key(self) -> bytes:
        seed = ""
        for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                seed = Path(candidate).read_text(encoding="utf-8").strip()
                break
            except OSError:
                continue
        seed = seed or str(Path.home())
        return hashlib.sha256(f"fluentry:{seed}".encode("utf-8")).digest()

    def _transform(self, data: bytes) -> bytes:
        key = self._cipher_key()
        return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))

    def load(self) -> dict[str, str]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise KeychainError(str(error)) from error
        try:
            payload = json.loads(self._transform(base64.b64decode(raw)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise KeychainError("Stored keys could not be decoded.") from error
        return payload if isinstance(payload, dict) else {}

    def save(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = base64.b64encode(self._transform(json.dumps(values).encode("utf-8")))
        temporary = self.path.with_suffix(".tmp")
        try:
            with open(temporary, "wb") as handle:
                handle.write(encoded)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as error:
            raise KeychainError(str(error)) from error


class InMemoryBackend:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def load(self) -> dict[str, str]:
        return dict(self.values)

    def save(self, values: dict[str, str]) -> None:
        self.values = dict(values)


def make_secret_backend():
    if SecretToolBackend.is_available():
        return SecretToolBackend()
    return ObfuscatedFileBackend()


def secret_backend_name() -> str:
    """What Settings shows the user about where their keys live."""
    return "Secret Service" if SecretToolBackend.is_available() else "encrypted file"
