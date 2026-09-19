"""The Wayland insertion path, and the silent failures it used to have.

Every bug covered here reported success while doing nothing: a chord that
exited 0 without emitting a key, a clipboard write that blocked past its
own timeout, a readiness line naming a backend that was not the one in
use. Each test pins the behaviour that makes the failure visible instead.
"""

from __future__ import annotations

import subprocess

import pytest

from fluentry.models import keycodes
from fluentry.platform import clipboard as clipboard_module
from fluentry.platform import text_injection
from fluentry.platform.clipboard import CommandLineClipboard, make_clipboard
from fluentry.platform.libei_injection import LibeiBackend, keycode_for
from fluentry.platform.text_injection import (
    CLEARED_MODIFIER_CODES,
    YdotoolBackend,
    ydotool_keycode,
)


class RecordingRun:
    """Stands in for the module's `_run`, keeping the commands it was given."""

    def __init__(self, succeeds: bool = True) -> None:
        self.commands: list[list[str]] = []
        self.succeeds = succeeds

    def __call__(self, command, input_text=None, timeout=10.0) -> bool:
        self.commands.append(list(command))
        return self.succeeds


# --- ydotool ----------------------------------------------------------------


def test_ydotool_chords_are_keycodes_because_names_are_silently_ignored(monkeypatch):
    """`ydotool key ctrl+v` exits 0 and emits nothing at all.

    ydotool takes raw evdev keycodes; anything it cannot parse it treats as
    a pause. A name-based chord therefore looked like a successful paste
    while no key was ever pressed.
    """
    runs = RecordingRun()
    monkeypatch.setattr(text_injection, "_run", runs)

    assert YdotoolBackend().send_chord("v", ["ctrl"]) is True

    chord = runs.commands[-1]
    assert "ctrl+v" not in chord, "names reach ydotool as an unparseable pause"
    ctrl, v = keycodes.KEY_LEFTCTRL, keycodes.KEY_V
    assert chord[-4:] == [f"{ctrl}:1", f"{v}:1", f"{v}:0", f"{ctrl}:0"]


def test_ydotool_releases_a_chord_in_reverse_so_no_modifier_sticks(monkeypatch):
    """A chord that ends with the modifier still down wedges the keyboard."""
    runs = RecordingRun()
    monkeypatch.setattr(text_injection, "_run", runs)

    YdotoolBackend().send_chord("v", ["shift", "ctrl"])

    shift, ctrl, v = keycodes.KEY_LEFTSHIFT, keycodes.KEY_LEFTCTRL, keycodes.KEY_V
    assert runs.commands[-1][-6:] == [
        f"{shift}:1", f"{ctrl}:1", f"{v}:1",
        f"{v}:0", f"{ctrl}:0", f"{shift}:0",
    ]


def test_an_unmappable_chord_fails_rather_than_reporting_success(monkeypatch):
    runs = RecordingRun()
    monkeypatch.setattr(text_injection, "_run", runs)

    assert YdotoolBackend().send_chord("no-such-key") is False
    assert runs.commands == [], "nothing may be sent for a key we cannot name"


def test_ydotool_clears_the_modifiers_before_typing(monkeypatch):
    """xdotool has --clearmodifiers; ydotool has to be told.

    The dictation shortcut is Right Alt by default, which is AltGr on most
    layouts outside the US. Still held, it turns every injected character
    into a shortcut and nothing reaches the document.
    """
    runs = RecordingRun()
    monkeypatch.setattr(text_injection, "_run", runs)

    YdotoolBackend().type_text("hello")

    assert len(runs.commands) == 2
    clear, typing = runs.commands
    assert clear[:2] == ["ydotool", "key"]
    assert clear[2:] == [f"{code}:0" for code in CLEARED_MODIFIER_CODES]
    assert typing[:2] == ["ydotool", "type"]


def test_typing_is_paced_because_the_compositor_drops_a_faster_stream(monkeypatch):
    runs = RecordingRun()
    monkeypatch.setattr(text_injection, "_run", runs)

    YdotoolBackend().type_text("hello")

    typing = runs.commands[-1]
    delay = int(typing[typing.index("--key-delay") + 1])
    assert delay >= 20, "1ms drops characters between the kernel and the window"


def test_ydotool_keycode_lookup_is_case_insensitive_and_total():
    assert ydotool_keycode("Ctrl") == keycodes.KEY_LEFTCTRL
    assert ydotool_keycode("RETURN") == keycodes.KEY_ENTER
    assert ydotool_keycode("v") == keycodes.KEY_V
    assert ydotool_keycode("nonsense") is None


# --- libei ------------------------------------------------------------------


class FakeDevice:
    """Records the frames a chord produces."""

    name = "fake virtual keyboard"

    def __init__(self) -> None:
        self.events: list[tuple[int, bool]] = []
        self.frames = 0
        self.emulating = False
        self.stopped = False

    def start_emulating(self, sequence=None) -> "FakeDevice":
        self.emulating = True
        return self

    def keyboard_key(self, key: int, is_press: bool) -> "FakeDevice":
        self.events.append((key, is_press))
        return self

    def frame(self, timestamp=None) -> "FakeDevice":
        self.frames += 1
        return self

    def stop_emulating(self) -> "FakeDevice":
        self.stopped = True
        return self


def test_libei_sends_each_chord_event_as_its_own_frame():
    """A frame is one logical hardware event.

    Everything in a single frame lands in the same instant, and a
    compositor that has not applied the modifier yet sees a bare keypress.
    """
    backend = LibeiBackend()
    device = FakeDevice()
    backend._device = device

    assert backend.send_chord("v", ["ctrl"]) is True

    ctrl, v = keycodes.KEY_LEFTCTRL, keycodes.KEY_V
    assert device.events == [(ctrl, True), (v, True), (v, False), (ctrl, False)]
    assert device.frames == 4, "one frame per event, not one for the chord"
    assert device.stopped is True


def test_libei_declines_to_type_so_the_clipboard_carries_the_text():
    """The compositor does not grant the TEXT capability.

    Typing would mean guessing a key position for every character and
    hoping the user's layout agrees. Declining makes TypingService fall
    back to the clipboard, which carries the text exactly as transcribed.
    """
    assert LibeiBackend().type_text("anything") is False


def test_libei_rejects_a_chord_it_cannot_map():
    backend = LibeiBackend()
    backend._device = FakeDevice()
    assert backend.send_chord("no-such-key") is False
    assert backend._device.events == []


def test_libei_availability_never_asks_the_user_for_permission(monkeypatch):
    """A capability check must not spring a consent dialog on anyone.

    `--check` calls this to report what the machine supports, which has to
    stay a question rather than becoming a request.
    """
    oeffis = pytest.importorskip("libei.oeffis", reason="python-libei is optional")

    def explode(*args, **kwargs):
        raise AssertionError("is_available() must not negotiate a session")

    monkeypatch.setattr(oeffis.Oeffis, "create", explode)
    assert LibeiBackend.is_available() in (True, False)


def test_libei_keycode_lookup_matches_the_names_the_app_uses():
    assert keycode_for("ctrl") == keycodes.KEY_LEFTCTRL
    assert keycode_for("Return") == keycodes.KEY_ENTER
    assert keycode_for("v") == keycodes.KEY_V
    assert keycode_for("nope") is None


def test_the_portal_grant_is_stored_and_read_back():
    """Remembering the grant is what makes the dialog a one-time event."""
    LibeiBackend._write_token("a-restore-token")
    assert LibeiBackend._read_token() == "a-restore-token"

    path = LibeiBackend._token_path()
    assert path.stat().st_mode & 0o077 == 0, "the grant is this user's alone"

    LibeiBackend._write_token(None)
    assert LibeiBackend._read_token() is None


def test_a_paused_device_is_waited_for_rather_than_renegotiated(monkeypatch):
    """The compositor pauses an idle device and resumes it on demand.

    Treating that as a lost session opened a fresh portal session each
    time - needless churn, and a dialog again if the stored grant ever
    failed.
    """
    backend = LibeiBackend()
    resumed = FakeDevice()
    backend._sender = object()  # a live connection, just no current device
    backend._device = None
    monkeypatch.setattr(LibeiBackend, "_await_resume", lambda self, **kw: resumed)

    def must_not_negotiate(self):
        raise AssertionError("a paused device must not trigger a negotiation")

    monkeypatch.setattr(LibeiBackend, "_negotiate_session", must_not_negotiate)
    assert backend._ensure_device() is resumed


# --- clipboard --------------------------------------------------------------


class RecordingSubprocess:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), **kwargs})
        return subprocess.CompletedProcess(command, 0, b"", b"")


def test_wl_copy_is_given_the_text_as_an_argument_not_on_stdin(monkeypatch):
    """`wl-copy` forks a process that serves the selection.

    That child inherited the stdin pipe and never closed it, so the call
    blocked past its own timeout and the write never returned.
    """
    runs = RecordingSubprocess()
    monkeypatch.setattr(clipboard_module.subprocess, "run", runs)
    monkeypatch.setattr(
        CommandLineClipboard, "_detect_tools", staticmethod(lambda: (["wl-copy"], ["wl-paste"]))
    )

    assert CommandLineClipboard().write_text("hello") is True

    call = runs.calls[-1]
    assert call["command"] == ["wl-copy", "--", "hello"]
    assert call["input"] is None, "stdin is what used to hang the write"


def test_the_clipboard_daemon_outlives_the_call_that_started_it(monkeypatch):
    """Killed with its parent, the daemon takes the selection with it."""
    runs = RecordingSubprocess()
    monkeypatch.setattr(clipboard_module.subprocess, "run", runs)
    monkeypatch.setattr(
        CommandLineClipboard, "_detect_tools", staticmethod(lambda: (["wl-copy"], ["wl-paste"]))
    )

    CommandLineClipboard().write_text("hello")

    call = runs.calls[-1]
    assert call["start_new_session"] is True
    assert call["stdout"] is subprocess.DEVNULL
    assert call["stderr"] is subprocess.DEVNULL


def test_the_x11_tools_still_get_the_text_on_stdin(monkeypatch):
    """Only `wl-copy` takes the text as an argument."""
    runs = RecordingSubprocess()
    monkeypatch.setattr(clipboard_module.subprocess, "run", runs)
    monkeypatch.setattr(
        CommandLineClipboard,
        "_detect_tools",
        staticmethod(lambda: (["xclip", "-selection", "clipboard"], ["xclip", "-o"])),
    )

    CommandLineClipboard().write_text("hello")

    call = runs.calls[-1]
    assert call["command"] == ["xclip", "-selection", "clipboard"]
    assert call["input"] == b"hello"


def test_wayland_gets_wl_copy_rather_than_qt(monkeypatch):
    """Qt cannot take a Wayland selection from the background.

    It needs an input serial from a focused surface, and this app is never
    the focused window when it has something to paste. It caches the write,
    answers True, and publishes nothing.
    """
    monkeypatch.setattr(clipboard_module, "session_type", lambda: "wayland", raising=False)
    monkeypatch.setattr(CommandLineClipboard, "is_available", staticmethod(lambda: True))

    def qt_must_not_be_used(*args, **kwargs):
        raise AssertionError("Qt's clipboard cannot publish from the background")

    monkeypatch.setattr(clipboard_module, "QtClipboard", qt_must_not_be_used)
    assert isinstance(make_clipboard(), CommandLineClipboard)


# --- readiness --------------------------------------------------------------


def test_readiness_names_the_backend_that_will_actually_be_used(settings, monkeypatch):
    """It used to report the first one installed.

    `available_backends` is a fixed list; the chooser reorders it on
    Wayland. The two disagreed, so the Welcome screen said "xdotool" while
    ydotool did the typing.
    """
    from fluentry import app as app_module

    state = app_module.AppState(settings=settings, start_services=False)
    monkeypatch.setattr(app_module, "available_backends", lambda: ["xdotool", "ydotool"])
    monkeypatch.setattr(
        app_module,
        "make_injection_backend",
        lambda session=None: type("Chosen", (), {"name": "libei"})(),
    )

    detail = next(
        detail
        for label, _ok, detail in state.readiness_report()
        if label == "Text insertion"
    )
    assert "libei" in detail
    assert "xdotool" not in detail
