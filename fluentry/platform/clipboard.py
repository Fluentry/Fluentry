"""Clipboard access and preservation.

`ClipboardService` and `PreservedPasteboardSnapshot`, retargeted at the Linux
selection model.

The important behaviour is the *preservation* rule, which is unchanged: when
the app borrows the clipboard to paste a transcription, it restores what was
there before — but only if the user has not copied something since. Equal text
is not ownership: the user may have copied that same text with different
formats, and the newer clipboard always wins. One answer is
a change counter; here a monotonically increasing generation counter
plays the same role.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Protocol

TEXT_MIME = "text/plain;charset=utf-8"


class Clipboard(Protocol):
    @property
    def change_count(self) -> int: ...

    def read_text(self) -> str | None: ...

    def write_text(self, text: str) -> bool: ...

    def read_all(self) -> list[dict[str, bytes]]: ...

    def write_all(self, items: list[dict[str, bytes]]) -> bool: ...

    def clear(self) -> None: ...


class InMemoryClipboard:
    """Full-fidelity in-process clipboard, used by tests and headless runs."""

    def __init__(self) -> None:
        self._items: list[dict[str, bytes]] = []
        self._change_count = 0
        self._lock = threading.Lock()

    @property
    def change_count(self) -> int:
        with self._lock:
            return self._change_count

    def read_text(self) -> str | None:
        with self._lock:
            for item in self._items:
                payload = item.get(TEXT_MIME)
                if payload is not None:
                    return payload.decode("utf-8", "replace")
        return None

    def write_text(self, text: str) -> bool:
        return self.write_all([{TEXT_MIME: text.encode("utf-8")}])

    def read_all(self) -> list[dict[str, bytes]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def write_all(self, items: list[dict[str, bytes]]) -> bool:
        with self._lock:
            self._items = [dict(item) for item in items]
            self._change_count += 1
        return True

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._change_count += 1


class CommandLineClipboard:
    """Backed by `wl-copy`/`wl-paste` on Wayland, or `xclip`/`xsel` on X11.

    Only `text/plain` round-trips through the CLI tools, so richer formats
    present on the user's clipboard are preserved as text.
    """

    def __init__(self) -> None:
        self._change_count = 0
        self._lock = threading.Lock()
        self._copy, self._paste = self._detect_tools()

    @staticmethod
    def _detect_tools() -> tuple[list[str] | None, list[str] | None]:
        if shutil.which("wl-copy") and shutil.which("wl-paste"):
            return ["wl-copy"], ["wl-paste", "--no-newline"]
        if shutil.which("xclip"):
            return (
                ["xclip", "-selection", "clipboard"],
                ["xclip", "-selection", "clipboard", "-o"],
            )
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]
        return None, None

    @staticmethod
    def is_available() -> bool:
        copy, paste = CommandLineClipboard._detect_tools()
        return copy is not None and paste is not None

    @property
    def change_count(self) -> int:
        with self._lock:
            return self._change_count

    def read_text(self) -> str | None:
        if self._paste is None:
            return None
        try:
            result = subprocess.run(
                self._paste, capture_output=True, text=True, timeout=3, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout or None

    @property
    def _copy_takes_argument(self) -> bool:
        """`wl-copy` accepts the text as an argument; the X11 tools do not."""
        return bool(self._copy) and self._copy[0] == "wl-copy"

    def write_text(self, text: str) -> bool:
        if self._copy is None:
            return False
        # These tools fork a process that serves the selection for as long as
        # the app owns it, and getting that handover right needs three things.
        # Passing the text as an argument keeps the daemon off a stdin pipe it
        # would never close - piping it made `run` block past its own timeout,
        # so the write never returned. `start_new_session` puts the daemon in
        # its own process group so it outlives this call instead of dying with
        # it and leaving the clipboard empty. Detached streams keep it off our
        # stdout, which it would otherwise hold open.
        command = list(self._copy)
        payload: bytes | None = text.encode("utf-8")
        if self._copy_takes_argument:
            command += ["--", text]
            payload = None
        try:
            subprocess.run(
                command,
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=True,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        with self._lock:
            self._change_count += 1
        return True

    def read_all(self) -> list[dict[str, bytes]]:
        text = self.read_text()
        if text is None:
            return []
        return [{TEXT_MIME: text.encode("utf-8")}]

    def write_all(self, items: list[dict[str, bytes]]) -> bool:
        for item in items:
            payload = item.get(TEXT_MIME)
            if payload is not None:
                return self.write_text(payload.decode("utf-8", "replace"))
        return self.write_text("")

    def clear(self) -> None:
        self.write_text("")


class _ClipboardBridge:
    """Runs clipboard work on the GUI thread.

    Qt's clipboard may only be touched from the thread that owns the
    application. Dictation finishes on a worker thread, and a write from
    there returns without error while the Wayland selection is never
    published — the clipboard simply stays empty, and every caller is told
    it succeeded. Hopping threads is what makes the write real.
    """

    def __init__(self, application) -> None:
        from PySide6.QtCore import QObject, Qt, Signal

        class _Bridge(QObject):
            invoke = Signal(object, object)

            def __init__(self) -> None:
                super().__init__()
                # Blocking, so the caller sees the result and the ordering
                # against the paste chord that follows it is preserved.
                self.invoke.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

            @staticmethod
            def _run(work, result) -> None:
                try:
                    result.append(work())
                except Exception:
                    result.append(None)

        self._application = application
        self._bridge = _Bridge()

    def run(self, work):
        from PySide6.QtCore import QThread

        if QThread.currentThread() == self._application.thread():
            return work()
        result: list = []
        self._bridge.invoke.emit(work, result)
        return result[0] if result else None


class QtClipboard:
    """Qt's clipboard, which keeps every MIME type on X11 and Wayland alike."""

    def __init__(self, application=None) -> None:
        from PySide6.QtGui import QGuiApplication

        self._application = application or QGuiApplication.instance()
        if self._application is None:
            raise RuntimeError("A QGuiApplication must exist before using QtClipboard.")
        self._clipboard = self._application.clipboard()
        self._change_count = 0
        self._clipboard.dataChanged.connect(self._bump)
        self._bridge = _ClipboardBridge(self._application)

    def _bump(self) -> None:
        self._change_count += 1

    @property
    def change_count(self) -> int:
        return self._change_count

    def read_text(self) -> str | None:
        return self._bridge.run(lambda: self._clipboard.text() or None)

    def write_text(self, text: str) -> bool:
        return self.write_all([{TEXT_MIME: text.encode("utf-8")}])

    def read_all(self) -> list[dict[str, bytes]]:
        def work() -> list[dict[str, bytes]]:
            mime_data = self._clipboard.mimeData()
            if mime_data is None:
                return []
            item: dict[str, bytes] = {}
            for fmt in mime_data.formats():
                item[fmt] = bytes(mime_data.data(fmt))
            return [item] if item else []

        return self._bridge.run(work) or []

    def write_all(self, items: list[dict[str, bytes]]) -> bool:
        from PySide6.QtCore import QMimeData

        def work() -> bool:
            if not items:
                self._clipboard.clear()
                return True
            mime_data = QMimeData()
            for fmt, payload in items[0].items():
                mime_data.setData(fmt, payload)
            self._clipboard.setMimeData(mime_data)
            return True

        return bool(self._bridge.run(work))

    def clear(self) -> None:
        self._bridge.run(self._clipboard.clear)


def make_clipboard() -> Clipboard:
    """Qt's clipboard everywhere it works; a helper process on Wayland.

    Wayland only lets a client take the selection with an input serial from a
    focused surface, and this app is never the focused window when it has
    something to paste — the tray is its home and the overlay refuses focus
    on purpose. Qt therefore caches the write locally, reports success, and
    publishes nothing. `wl-copy` forks a process whose whole job is to hold
    the selection, which is the only thing that works from the background.
    """
    from .system_capabilities import SESSION_WAYLAND, session_type

    if session_type() == SESSION_WAYLAND and CommandLineClipboard.is_available():
        return CommandLineClipboard()
    try:
        return QtClipboard()
    except Exception:
        pass
    if CommandLineClipboard.is_available():
        return CommandLineClipboard()
    return InMemoryClipboard()


class ClipboardService:
    """Thin logging wrapper around whichever backend is in use."""

    def __init__(self, clipboard: Clipboard | None = None) -> None:
        self.clipboard = clipboard if clipboard is not None else InMemoryClipboard()

    def copy_to_clipboard(self, text: str) -> bool:
        if not text:
            return False
        return self.clipboard.write_text(text)

    def get_from_clipboard(self) -> str | None:
        return self.clipboard.read_text()


@dataclass
class PreservedClipboardSnapshot:
    """Every readable representation, not just the text used for pasting."""

    items: list[dict[str, bytes]] = field(default_factory=list)

    @staticmethod
    def capture(clipboard: Clipboard) -> "PreservedClipboardSnapshot":
        return PreservedClipboardSnapshot(clipboard.read_all())

    def restore(self, clipboard: Clipboard, if_unchanged_since: int) -> bool:
        # Equal text is not ownership: the user may have copied that same text
        # with different formats since our write. Their clipboard always wins.
        if clipboard.change_count != if_unchanged_since:
            return False
        if not self.items:
            # Nothing was there to put back. Clearing would only destroy the
            # transcript we just wrote - and that copy is the user's fallback
            # when the paste chord does not reach the focused window. An
            # empty clipboard is not worth losing their words for.
            return False
        return clipboard.write_all(self.items)
