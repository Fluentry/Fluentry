"""One Fluentry per session.

Nothing stopped a second copy starting. Both grabbed the global hotkey,
both put an icon in the tray, both wrote to the same history, and a single
press of Right Alt started two recordings against one microphone. The
symptom is not an error message — it is an app that behaves strangely in
ways nobody can attribute to a second copy they did not know was running.

A second launch hands over to the one already here and exits, which is
what someone clicking the launcher a second time actually wants.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..logging_setup import get_logger

_log = get_logger("instance")

#: Per-user, so two people sharing a machine each get their own.
def socket_name(uid: int | None = None) -> str:
    import os

    return f"fluentry-{uid if uid is not None else os.getuid()}"


class SingleInstance(QObject):
    """Owns the lock, and reports when another launch asks to be seen."""

    another_launch = Signal()

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self._name = name or socket_name()
        self._server: QLocalServer | None = None

    def claim(self) -> bool:
        """True when this process is the one instance, False when it is not."""
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(300):
            # Someone is already home; ask them to show themselves.
            probe.write(b"show")
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            _log.info("another instance is already running; handing over to it")
            return False

        # A crash leaves the socket file behind and every later start would
        # then think it is the second copy. Nothing is listening on it, which
        # the failed connect above just established, so it is safe to clear.
        QLocalServer.removeServer(self._name)
        server = QLocalServer()
        if not server.listen(self._name):
            _log.warning("could not claim the instance lock: %s", server.errorString())
            # Better to run than to refuse to start over a lock problem.
            return True
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _on_connection(self) -> None:
        connection = self._server.nextPendingConnection() if self._server else None
        if connection is None:
            return
        connection.readyRead.connect(lambda: self.another_launch.emit())
        connection.disconnected.connect(connection.deleteLater)

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._name)
            self._server = None
