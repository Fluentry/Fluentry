"""Input injection through libei and the RemoteDesktop portal.

This is the path GNOME sanctions. `ydotool` creates a uinput device and
hopes the compositor treats it as a keyboard; mutter reads that device and
then declines to deliver its events to the focused window, silently. With
libei the compositor creates the virtual device itself, so what arrives is
input it already trusts.

The session is opened once and kept for the life of the app: the portal
asks the user's permission, and asking again on every dictation would be
both slow and insufferable.

Only `send_chord` is implemented. Typing character by character would mean
mapping text onto evdev key positions and hoping the user's layout agrees,
which is the class of bug this module exists to leave behind — so
`type_text` declines, and `TypingService` falls back to putting the text on
the clipboard and pasting it with a chord, which is layout-independent.
"""

from __future__ import annotations

import select
import threading
import time
from typing import Sequence

from ..logging_setup import get_logger
from ..models import keycodes
from ..models.keycodes import ModifierFlags

_log = get_logger("libei")

#: Names the rest of the app uses, mapped onto evdev key positions.
KEYCODES: dict[str, int] = {
    "ctrl": keycodes.KEY_LEFTCTRL,
    "control": keycodes.KEY_LEFTCTRL,
    "shift": keycodes.KEY_LEFTSHIFT,
    "alt": keycodes.KEY_LEFTALT,
    "super": keycodes.KEY_LEFTMETA,
    "meta": keycodes.KEY_LEFTMETA,
    "return": keycodes.KEY_ENTER,
    "enter": keycodes.KEY_ENTER,
    "tab": keycodes.KEY_TAB,
    "escape": keycodes.KEY_ESC,
    "esc": keycodes.KEY_ESC,
    "space": keycodes.KEY_SPACE,
    "backspace": keycodes.KEY_BACKSPACE,
    **{name.lower(): code for code, name in keycodes.QWERTY_FALLBACK.items()},
}

#: A chord whose events all land in the same instant can be seen before the
#: modifier has been applied, leaving a bare keypress.
CHORD_GAP_SECONDS = 0.02
PORTAL_TIMEOUT_SECONDS = 30.0
DEVICE_TIMEOUT_SECONDS = 10.0
#: How long to wait for a paused device to come back before giving up on
#: the session and negotiating a new one.
RESUME_TIMEOUT_SECONDS = 5.0


def keycode_for(name: str) -> int | None:
    return KEYCODES.get(name.strip().lower())


class LibeiBackend:
    """Sends chords through a portal-granted virtual keyboard."""

    name = "libei"

    MODIFIER_NAMES = {
        ModifierFlags.CONTROL: "ctrl",
        ModifierFlags.SHIFT: "shift",
        ModifierFlags.ALT: "alt",
        ModifierFlags.SUPER: "super",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session = None
        self._sender = None
        self._device = None

    # --- remembered consent ----------------------------------------------

    @staticmethod
    def _token_path():
        from ..persistence.defaults import state_home

        return state_home() / "libei-restore-token"

    @classmethod
    def _read_token(cls) -> str | None:
        try:
            token = cls._token_path().read_text().strip()
        except OSError:
            return None
        return token or None

    @classmethod
    def _write_token(cls, token: str | None) -> None:
        path = cls._token_path()
        try:
            if not token:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token)
            # The grant is this user's alone.
            path.chmod(0o600)
        except OSError as error:
            _log.warning("could not store the portal token: %s", error)

    def _negotiate_session(self):
        """A session, asking the user only when it has to.

        The portal can remember a grant, but only through the D-Bus path,
        which needs PyGObject. Without it the fallback still works and simply
        asks again on every launch.
        """
        try:
            from libei import portal
        except Exception:
            portal = None

        if portal is not None and portal.is_available():
            token = self._read_token()
            for attempt_token in (token, None) if token else (None,):
                try:
                    session = portal.RemoteDesktopSession.negotiate(
                        devices=portal.DeviceType.KEYBOARD,
                        persist_mode=portal.PersistMode.UNTIL_REVOKED,
                        restore_token=attempt_token,
                    )
                except Exception as error:
                    # A token the user has revoked is refused; drop it and
                    # ask again rather than failing outright.
                    _log.info(
                        "portal negotiation failed%s: %s",
                        " with the stored token" if attempt_token else "",
                        error,
                    )
                    if attempt_token:
                        self._write_token(None)
                    continue
                self._write_token(getattr(session, "restore_token", None))
                _log.info(
                    "portal session ready (%s)",
                    "remembered" if attempt_token else "newly granted",
                )
                return session, session.eis_fd
            return None, None

        _log.info(
            "PyGObject is not installed, so the permission cannot be remembered; "
            "install python3-gi to be asked only once"
        )
        return self._negotiate_with_oeffis()

    def _negotiate_with_oeffis(self):
        from libei import oeffis

        session = oeffis.Oeffis.create(devices=oeffis.DeviceType.KEYBOARD)
        deadline = time.monotonic() + PORTAL_TIMEOUT_SECONDS
        while True:
            if time.monotonic() > deadline:
                _log.error("portal did not answer within %.0fs", PORTAL_TIMEOUT_SECONDS)
                return None, None
            select.select([session.fd], [], [], 1.0)
            try:
                if session.dispatch():
                    return session, session.eis_fd
            except Exception as error:
                _log.error("portal refused: %s: %s", type(error).__name__, error)
                return None, None

    @staticmethod
    def is_available() -> bool:
        """Whether the libraries and the portal are both present.

        This does not open a session: that asks the user for permission, and
        a capability check must never surprise them with a dialog.
        """
        try:
            from libei import oeffis
        except Exception:
            return False
        try:
            return bool(oeffis.is_available())
        except Exception:
            return False

    # --- session ----------------------------------------------------------

    def _await_resume(self, timeout: float = RESUME_TIMEOUT_SECONDS):
        """Wait for a paused device to come back.

        The compositor pauses a device when it is not in use and resumes it
        on demand. That is not a lost session and must not be treated as
        one: negotiating again would open a second portal session, and ask
        the user for permission afresh if the stored grant ever failed.
        """
        from libei import ei

        if self._sender is None:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            select.select([self._sender.fd], [], [], 0.5)
            try:
                self._sender.dispatch()
            except Exception as error:
                _log.warning("dispatch failed while waiting to resume: %s", error)
                return None
            for event in self._sender.events:
                if event.event_type is ei.EventType.DEVICE_RESUMED:
                    self._device = event.device
                    return self._device
                if event.event_type is ei.EventType.DISCONNECT:
                    _log.warning("libei session disconnected while paused")
                    self._session = self._sender = self._device = None
                    return None
        _log.info("device did not resume within %.0fs", timeout)
        return None

    def _ensure_device(self):
        """Open the session on first use, then reuse it."""
        if self._device is not None:
            return self._device
        if self._sender is not None:
            # Paused, not lost. Wait rather than re-negotiating.
            device = self._await_resume()
            if device is not None:
                return device
        from libei import ei

        session, eis_fd = self._negotiate_session()
        if session is None or eis_fd is None:
            return None

        sender = ei.Sender.create_for_fd(eis_fd, name="fluentry")
        device = None
        deadline = time.monotonic() + DEVICE_TIMEOUT_SECONDS
        while device is None and time.monotonic() < deadline:
            select.select([sender.fd], [], [], 1.0)
            sender.dispatch()
            for event in sender.events:
                if event.event_type is ei.EventType.SEAT_ADDED:
                    event.seat.bind((ei.DeviceCapability.KEYBOARD,))
                elif event.event_type is ei.EventType.DEVICE_RESUMED:
                    # Devices arrive paused; only a resumed one accepts input.
                    device = event.device
                    break
        if device is None:
            _log.error("the compositor never resumed a keyboard device")
            return None

        # Held so the session outlives this call; dropping either closes it.
        self._session, self._sender, self._device = session, sender, device
        _log.info("libei ready: %s", device.name)
        return device

    def _drain(self) -> None:
        """Keep up with pause/resume so a stale device is never used."""
        if self._sender is None:
            return
        from libei import ei

        try:
            self._sender.dispatch()
            for event in self._sender.events:
                if event.event_type is ei.EventType.DEVICE_PAUSED:
                    _log.info("device paused")
                    self._device = None
                elif event.event_type is ei.EventType.DEVICE_RESUMED:
                    self._device = event.device
                elif event.event_type is ei.EventType.DISCONNECT:
                    _log.warning("libei session disconnected")
                    self._session = self._sender = self._device = None
        except Exception as error:
            _log.warning("dispatch failed, dropping session: %s", error)
            self._session = self._sender = self._device = None

    # --- injection --------------------------------------------------------

    def type_text(self, text: str) -> bool:
        """Declined on purpose, so the clipboard path is used instead.

        The compositor does not grant libei's TEXT capability here, so
        typing would mean guessing key positions for every character. The
        clipboard carries the text exactly as transcribed.
        """
        return False

    def send_chord(self, key: str, modifiers: Sequence[str] = ()) -> bool:
        codes = [keycode_for(name) for name in (*modifiers, key)]
        if any(code is None for code in codes):
            _log.error("no key position for chord %s+%s", "+".join(modifiers), key)
            return False

        with self._lock:
            self._drain()
            device = self._ensure_device()
            if device is None:
                return False
            try:
                device.start_emulating()
                # One frame per event: each commits a single logical
                # keypress, and the gaps let the modifier take effect first.
                for code in codes:
                    device.keyboard_key(code, True).frame()
                    time.sleep(CHORD_GAP_SECONDS)
                for code in reversed(codes):
                    device.keyboard_key(code, False).frame()
                    time.sleep(CHORD_GAP_SECONDS)
                device.stop_emulating()
            except Exception as error:
                _log.error("chord failed: %s: %s", type(error).__name__, error)
                self._session = self._sender = self._device = None
                return False
        return True
