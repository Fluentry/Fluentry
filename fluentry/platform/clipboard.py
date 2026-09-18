"""Clipboard access and preservation.

`ClipboardService` and `PreservedPasteboardSnapshot`, retargeted at the Linux
selection model.

The important behaviour is the *preservation* rule, which is unchanged: when
the app borrows the clipboard to paste a transcription, it restores what was
there before — but only if the user has not copied something since. Equal text
is not ownership: the user may have copied that same text with different
formats, and their newer clipboard always wins. macOS answered this with
`NSPasteboard.changeCount`; here a monotonically increasing generation counter
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

    def write_text(self, text: str) -> bool:
        if self._copy is None:
            return False
        try:
            subprocess.run(
                self._copy, input=text.encode("utf-8"), timeout=3, check=True
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

    def _bump(self) -> None:
        self._change_count += 1

    @property
    def change_count(self) -> int:
        return self._change_count

    def read_text(self) -> str | None:
        return self._clipboard.text() or None

    def write_text(self, text: str) -> bool:
        self._clipboard.setText(text)
        return True

    def read_all(self) -> list[dict[str, bytes]]:
        mime_data = self._clipboard.mimeData()
        if mime_data is None:
            return []
        item: dict[str, bytes] = {}
        for fmt in mime_data.formats():
            item[fmt] = bytes(mime_data.data(fmt))
        return [item] if item else []

    def write_all(self, items: list[dict[str, bytes]]) -> bool:
        from PySide6.QtCore import QMimeData

        if not items:
            self._clipboard.clear()
            return True
        mime_data = QMimeData()
        for fmt, payload in items[0].items():
            mime_data.setData(fmt, payload)
        self._clipboard.setMimeData(mime_data)
        return True

    def clear(self) -> None:
        self._clipboard.clear()


def make_clipboard() -> Clipboard:
    try:
        return QtClipboard()
    except Exception:
        pass
    if CommandLineClipboard.is_available():
        return CommandLineClipboard()
    return InMemoryClipboard()


class ClipboardService:
    """Thin logging wrapper, matching the macOS `ClipboardService` API."""

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
            clipboard.clear()
            return True
        return clipboard.write_all(self.items)
