"""Typing into a terminal uses Ctrl+Shift+V, not Ctrl+V.

A terminal reads Ctrl+V as quoted-insert and shows a literal ^V; its paste
is Ctrl+Shift+V. Fluentry therefore has to know when the focused window is
a terminal - which on GNOME Wayland means the Fluentry Focus extension,
since the desktop hides the focused window from ordinary clients.
"""

from __future__ import annotations

import pytest

from fluentry.platform import active_window
from fluentry.platform.active_window import gnome_context
from fluentry.platform.text_injection import RecordingBackend, TypingService
from fluentry.services.literal_formatting import is_terminal_app


# --- terminal detection -----------------------------------------------------


@pytest.mark.parametrize(
    "app",
    ["gnome-terminal-server", "org.gnome.Console", "kgx", "ptyxis", "konsole",
     "alacritty", "kitty", "foot", "wezterm", "xterm"],
)
def test_terminals_are_recognised(app):
    assert is_terminal_app(app_name=app)


@pytest.mark.parametrize(
    "app",
    ["org.gnome.TextEditor", "code", "firefox", "org.gnome.Nautilus", "slack", ""],
)
def test_non_terminals_are_not(app):
    assert not is_terminal_app(app_name=app)


# --- the paste chord --------------------------------------------------------


def test_a_terminal_gets_ctrl_shift_v():
    backend = RecordingBackend()
    service = TypingService(backend=backend)
    service.send_paste_chord(into_terminal=True)
    key, mods = backend.chords[-1]
    assert key == service.paste_key_name
    assert set(mods) == {"ctrl", "shift"}


def test_everything_else_gets_ctrl_v():
    backend = RecordingBackend()
    service = TypingService(backend=backend)
    service.send_paste_chord(into_terminal=False)
    key, mods = backend.chords[-1]
    assert tuple(mods) == ("ctrl",), "no stray shift outside terminals"


def test_the_clipboard_paste_passes_the_terminal_flag_through():
    from fluentry.platform.clipboard import InMemoryClipboard

    backend = RecordingBackend()
    service = TypingService(
        backend=backend, clipboard=InMemoryClipboard(), paste_settle_seconds=0,
        clipboard_settle_seconds=0,
    )
    service.insert_via_clipboard("cd ~/project", into_terminal=True)
    _key, mods = backend.chords[-1]
    assert set(mods) == {"ctrl", "shift"}


# --- the GNOME focus provider (parsing gdbus output) ------------------------


def test_gnome_context_reads_the_focused_terminal(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(active_window, "_GNOME_FOCUS_ABSENT_UNTIL", 0.0)
    monkeypatch.setattr(
        active_window, "_run",
        lambda command: "('gnome-terminal-server', 'user@host: ~/project')\n",
    )
    ctx = gnome_context()
    assert ctx is not None
    assert ctx.app_id == "gnome-terminal-server"
    assert is_terminal_app(ctx.app_name, ctx.app_id, ctx.title)


def test_gnome_context_is_none_without_the_extension(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(active_window, "_GNOME_FOCUS_ABSENT_UNTIL", 0.0)
    monkeypatch.setattr(active_window, "_run", lambda command: None)
    assert gnome_context() is None


def test_gnome_context_stops_spawning_when_absent(monkeypatch):
    """The costly part: not a gdbus process per dictation when it is absent."""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setattr(active_window, "_GNOME_FOCUS_ABSENT_UNTIL", 0.0)
    calls = []
    monkeypatch.setattr(active_window, "_run", lambda command: calls.append(1))
    gnome_context()
    gnome_context()
    gnome_context()
    assert len(calls) == 1, "after one miss it backs off instead of asking again"


def test_gnome_context_is_skipped_off_gnome(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    called = []
    monkeypatch.setattr(active_window, "_run", lambda command: called.append(1))
    assert gnome_context() is None
    assert not called, "no gdbus on non-GNOME desktops"
