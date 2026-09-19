import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Point every XDG directory at a throwaway tree before anything imports the
# modules that resolve them. Without this a test run writes into the state,
# config and data directories of whoever is running it - the log handler in
# particular appends real lines to their fluentry.log, which makes a live
# debugging session unreadable.
_SANDBOX = tempfile.mkdtemp(prefix="fluentry-tests-")
for _variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    _directory = Path(_SANDBOX) / _variable.split("_")[1].lower()
    _directory.mkdir(parents=True, exist_ok=True)
    os.environ[_variable] = str(_directory)

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
