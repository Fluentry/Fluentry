"""One Fluentry per session.

Nothing stopped a second copy starting. Both grabbed the global hotkey,
both put an icon in the tray, both wrote to the same history, and a single
press of Right Alt started two recordings against one microphone. The
symptom is not an error message — it is an app behaving strangely in ways
nobody attributes to a second copy they did not know was running.

The lock is an abstract unix socket: a Linux address that lives in the
kernel rather than the filesystem, so it is released the moment the
process dies. A crash therefore cannot leave a lock behind that stops the
app ever starting again, which a lock file can and does.

It deliberately uses the standard library rather than QtNetwork. That is a
separate PySide6 module, absent from some distributions' packages, and a
missing import there stopped the whole app from starting — a high price
for a lock.
"""

from __future__ import annotations

import os
import socket
import threading
import time

from PySide6.QtCore import QObject, Signal

from ..logging_setup import get_logger

_log = get_logger("instance")


def socket_name(uid: int | None = None) -> str:
    """Per-user, so two people sharing a machine each get their own."""
    return f"fluentry-{uid if uid is not None else os.getuid()}"


class SingleInstance(QObject):
    """Owns the lock, and reports when another launch asks to be seen."""

    another_launch = Signal()

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        # A leading NUL puts this in the abstract namespace.
        self._address = "\0" + (name or socket_name())
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._releasing = False

    def claim(self, attempts: int = 3) -> bool:
        """True when this process is the one instance, False when it is not."""
        for attempt in range(attempts):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(self._address)
                break
            except OSError:
                server.close()
                # The address being taken is not proof anybody is home. A
                # copy shutting down still holds it for a moment, and
                # treating that as "already running" would refuse to start
                # a replacement launched right after a quit.
                if self._someone_answers():
                    self._ask_the_other_one_to_show_itself()
                    _log.info("another instance is already running; handing over to it")
                    return False
                if attempt + 1 < attempts:
                    time.sleep(0.1)
            except Exception as error:
                server.close()
                # Better to run than to refuse to start over a lock problem.
                _log.warning("could not claim the instance lock: %s", error)
                return True
        else:
            _log.warning("the instance lock stayed busy with nobody listening")
            return True
        server.listen(1)
        self._server = server
        self._thread = threading.Thread(
            target=self._accept_loop, name="fluentry.instance", daemon=True
        )
        self._thread.start()
        return True

    def _someone_answers(self) -> bool:
        """Whether a live instance is actually listening on the address."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(self._address)
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _ask_the_other_one_to_show_itself(self) -> None:
        """Clicking the launcher twice means "show me the app", not "fail"."""
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect(self._address)
            probe.sendall(b"show")
        except OSError:
            pass
        finally:
            probe.close()

    def _accept_loop(self) -> None:
        while self._server is not None:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return  # closed on the way out
            with connection:
                try:
                    connection.recv(16)
                except OSError:
                    continue
            if self._releasing:
                return  # that was our own wake-up, not a second launch
            # Queued across to the GUI thread by the connection type the
            # application sets up; nothing here touches a widget.
            self.another_launch.emit()

    def release(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        self._releasing = True
        # Closing a socket another thread is blocked in accept() on does not
        # wake it, and the address stays bound and connectable until it
        # returns - so a relaunch straight after a quit would decide a copy
        # was still running. One connection to ourselves unblocks it.
        try:
            waker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            waker.settimeout(0.5)
            waker.connect(self._address)
            waker.close()
        except OSError:
            pass
        server.close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
