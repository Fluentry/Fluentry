"""Enabling and reporting the Fluentry Focus GNOME extension.

The behaviour that matters: off GNOME this does nothing; on GNOME it
enables the bundled extension for the user when it is installed but not yet
running, and it never pretends the still-required re-login has happened.
The `gsettings`/`gdbus`/filesystem calls are stubbed so the decision logic
is what is under test.
"""

from __future__ import annotations

import pytest

from fluentry.platform import gnome_extension as ext
from fluentry.platform.gnome_extension import TerminalSupport


@pytest.fixture
def gnome(monkeypatch):
    """A GNOME session with the extension installed, service down, not enabled."""
    monkeypatch.setattr(ext, "_on_gnome", lambda: True)
    monkeypatch.setattr(ext, "files_present", lambda: True)
    monkeypatch.setattr(ext, "service_live", lambda: False)
    store = {"enabled": []}
    monkeypatch.setattr(ext, "_enabled_list", lambda: list(store["enabled"]))

    def fake_enable():
        if ext.UUID not in store["enabled"]:
            store["enabled"].append(ext.UUID)
        return True

    monkeypatch.setattr(ext, "_enable_in_settings", fake_enable)
    return store


# --- off GNOME --------------------------------------------------------------


def test_not_gnome_is_a_no_op(monkeypatch):
    monkeypatch.setattr(ext, "_on_gnome", lambda: False)
    # Should not even look at the filesystem or settings.
    monkeypatch.setattr(ext, "files_present", lambda: pytest.fail("looked at files"))
    assert ext.ensure_enabled() is TerminalSupport.NOT_NEEDED
    assert ext.status() is TerminalSupport.NOT_NEEDED


# --- on GNOME ---------------------------------------------------------------


def test_not_installed(monkeypatch):
    monkeypatch.setattr(ext, "_on_gnome", lambda: True)
    monkeypatch.setattr(ext, "files_present", lambda: False)
    assert ext.ensure_enabled() is TerminalSupport.NOT_INSTALLED


def test_active_when_service_is_live(monkeypatch):
    monkeypatch.setattr(ext, "_on_gnome", lambda: True)
    monkeypatch.setattr(ext, "files_present", lambda: True)
    monkeypatch.setattr(ext, "service_live", lambda: True)
    # Must not touch settings when it already works.
    monkeypatch.setattr(ext, "_enable_in_settings", lambda: pytest.fail("wrote settings"))
    assert ext.ensure_enabled() is TerminalSupport.ACTIVE
    assert ext.status() is TerminalSupport.ACTIVE


def test_enables_when_installed_but_not_running(gnome):
    assert gnome["enabled"] == []
    assert ext.ensure_enabled() is TerminalSupport.NEEDS_RELOGIN
    # It enabled itself for the next login.
    assert ext.UUID in gnome["enabled"]


def test_needs_relogin_without_rewriting_when_already_enabled(monkeypatch, gnome):
    gnome["enabled"].append(ext.UUID)
    monkeypatch.setattr(ext, "_enable_in_settings", lambda: pytest.fail("rewrote settings"))
    assert ext.ensure_enabled() is TerminalSupport.NEEDS_RELOGIN


def test_status_does_not_change_settings(monkeypatch, gnome):
    monkeypatch.setattr(ext, "_enable_in_settings", lambda: pytest.fail("status wrote settings"))
    assert ext.status() is TerminalSupport.NEEDS_RELOGIN
    assert gnome["enabled"] == []


# --- settings parsing -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@as []\n", []),
        ("['a@x', 'b@y']\n", ["a@x", "b@y"]),
        ("['fluentry-focus@fluentry.github.io']\n", ["fluentry-focus@fluentry.github.io"]),
        ("garbage", []),
        ("", []),
    ],
)
def test_enabled_list_parsing(monkeypatch, raw, expected):
    monkeypatch.setattr(ext, "_run", lambda cmd: raw if raw else None)
    assert ext._enabled_list() == expected


def test_enable_appends_to_existing(monkeypatch):
    calls = {}

    def fake_run(cmd):
        if cmd[:2] == ["gsettings", "get"]:
            return calls.get("set", "['keep@me']")
        if cmd[:2] == ["gsettings", "set"]:
            calls["set"] = cmd[-1]  # the list literal written
            return ""
        return None

    monkeypatch.setattr(ext, "_run", fake_run)
    assert ext._enable_in_settings() is True
    # Wrote a list that keeps the existing entry and adds ours.
    assert "keep@me" in calls["set"] and ext.UUID in calls["set"]
