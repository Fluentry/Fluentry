import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fluentry.persistence.defaults import Defaults  # noqa: E402
from fluentry.persistence.settings_store import SettingsStore  # noqa: E402


@pytest.fixture
def defaults() -> Defaults:
    """An isolated preferences domain, equivalent to a UserDefaults suite."""
    return Defaults()


@pytest.fixture
def settings(defaults: Defaults) -> SettingsStore:
    return SettingsStore(defaults=defaults)


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    """Keep every test out of the developer's real config and data directories."""
    for variable, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        path = tmp_path / folder
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(path))
    SettingsStore.reset_shared(None)
    yield
    SettingsStore.reset_shared(None)
