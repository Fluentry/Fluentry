"""Port of KeychainServiceCacheTests."""

import threading

import pytest

from fluentry.persistence.keychain import (
    InMemoryBackend,
    KeychainError,
    KeychainService,
    ObfuscatedFileBackend,
)


class ExpectedFailure(Exception):
    pass


def test_concurrent_stores_do_not_lose_updates():
    storage_lock = threading.Lock()
    storage: dict[str, str] = {}

    def load():
        with storage_lock:
            return dict(storage)

    def save(values):
        nonlocal storage
        with storage_lock:
            storage = dict(values)

    service = KeychainService(load=load, save=save)
    count = 200

    threads = [
        threading.Thread(target=service.store_key, args=(f"value-{index}", f"provider-{index}"))
        for index in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(service.fetch_all_keys()) == count


def test_failed_load_is_retried_instead_of_cached():
    load_count = 0

    def load():
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            raise ExpectedFailure()
        return {"openai": "secret"}

    service = KeychainService(load=load, save=lambda values: None)

    with pytest.raises(ExpectedFailure):
        service.fetch_all_keys()
    assert service.fetch_all_keys() == {"openai": "secret"}
    assert load_count == 2


def test_fetch_all_keys_loads_once_per_process():
    load_count = 0

    def load():
        nonlocal load_count
        load_count += 1
        return {"openai": "secret"}

    service = KeychainService(load=load, save=lambda values: None)

    assert service.fetch_all_keys() == {"openai": "secret"}
    assert service.fetch_all_keys() == {"openai": "secret"}
    assert load_count == 1


def test_successful_store_updates_the_cache_without_reloading():
    load_count = 0
    stored: dict[str, str] = {}

    def load():
        nonlocal load_count
        load_count += 1
        return {"openai": "old"}

    def save(values):
        nonlocal stored
        stored = dict(values)

    service = KeychainService(load=load, save=save)
    service.store_key("  new  ", for_provider := "groq")

    assert stored == {"openai": "old", "groq": "new"}, "keys are trimmed before storing"
    assert service.fetch_all_keys() == stored
    assert load_count == 1


def test_failed_store_keeps_the_previously_loaded_cache():
    load_count = 0

    def load():
        nonlocal load_count
        load_count += 1
        return {"openai": "old"}

    def save(values):
        raise ExpectedFailure()

    service = KeychainService(load=load, save=save)

    with pytest.raises(ExpectedFailure):
        service.store_key("new", "groq")
    assert service.fetch_all_keys() == {"openai": "old"}
    assert load_count == 1


def test_delete_updates_the_cache_without_reloading():
    load_count = 0
    stored: dict[str, str] = {}

    def load():
        nonlocal load_count
        load_count += 1
        return {"openai": "secret", "groq": "secret"}

    def save(values):
        nonlocal stored
        stored = dict(values)

    service = KeychainService(load=load, save=save)
    service.delete_key("openai")

    assert stored == {"groq": "secret"}
    assert service.fetch_all_keys() == stored
    assert load_count == 1


def test_store_refreshes_before_merging_external_changes():
    storage = {"openai": "old"}

    def load():
        return dict(storage)

    def save(values):
        nonlocal storage
        storage = dict(values)

    service = KeychainService(load=load, save=save)
    assert service.fetch_all_keys() == storage

    # Another instance writes a key while ours is cached.
    storage["anthropic"] = "external"
    service.store_key("new", "groq")

    assert storage == {"openai": "old", "anthropic": "external", "groq": "new"}
    assert service.fetch_all_keys() == storage


def test_explicit_refresh_observes_external_changes():
    load_count = 0
    storage = {"openai": "old"}

    def load():
        nonlocal load_count
        load_count += 1
        return dict(storage)

    def save(values):
        nonlocal storage
        storage = dict(values)

    service = KeychainService(load=load, save=save)
    assert service.fetch_all_keys() == {"openai": "old"}

    storage["openai"] = "external"
    service.refresh_cached_keys()

    assert service.fetch_all_keys() == {"openai": "external"}
    assert load_count == 2


def test_cached_read_does_not_wait_for_a_blocked_refresh():
    state_lock = threading.Lock()
    load_count = 0
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    refresh_finished = threading.Event()

    def load():
        nonlocal load_count
        with state_lock:
            load_count += 1
            current = load_count
        if current > 1:
            refresh_started.set()
            release_refresh.wait(5)
        return {"openai": "cached" if current == 1 else "refreshed"}

    service = KeychainService(load=load, save=lambda values: None)
    assert service.fetch_all_keys() == {"openai": "cached"}

    def refresh():
        try:
            service.refresh_cached_keys()
        finally:
            refresh_finished.set()

    threading.Thread(target=refresh, daemon=True).start()
    assert refresh_started.wait(2)

    # The blocked keyring must not stall a cached read.
    assert service.fetch_all_keys() == {"openai": "cached"}

    release_refresh.set()
    assert refresh_finished.wait(2)
    assert service.fetch_all_keys() == {"openai": "refreshed"}


def test_delete_all_keys_clears_the_store():
    backend = InMemoryBackend({"openai": "a", "groq": "b"})
    service = KeychainService(load=backend.load, save=backend.save)

    service.delete_all_keys()
    assert service.fetch_all_keys() == {}
    assert backend.values == {}


# --- file backend -----------------------------------------------------------


def test_obfuscated_file_backend_round_trips_and_is_not_plain_text(tmp_path):
    path = tmp_path / "keys.dat"
    backend = ObfuscatedFileBackend(path=path)

    assert backend.load() == {}, "a missing file is an empty store, not an error"
    backend.save({"openai": "sk-secret-value"})

    assert backend.load() == {"openai": "sk-secret-value"}
    raw = path.read_bytes()
    assert b"sk-secret-value" not in raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_obfuscated_file_backend_reports_corrupt_data(tmp_path):
    path = tmp_path / "keys.dat"
    path.write_bytes(b"not base64 at all !!!")
    with pytest.raises(KeychainError):
        ObfuscatedFileBackend(path=path).load()
