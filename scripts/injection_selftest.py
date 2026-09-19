#!/usr/bin/env python3
"""End-to-end test of the insertion path, into a window we own.

Opens a focused text field, runs the app's real `TypingService` against it,
and reports what actually arrived. Nothing is typed into the user's own
windows, so this can be run as often as it takes.

    python scripts/injection_selftest.py [standard|reliablePaste]
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from fluentry.persistence.settings_types import TextInsertionMode  # noqa: E402
from fluentry.platform.clipboard import make_clipboard  # noqa: E402
from fluentry.platform.text_injection import (  # noqa: E402
    TypingService,
    make_injection_backend,
)

SAMPLE = "the quick brown fox jumps over the lazy dog"


def main() -> int:
    mode = (
        TextInsertionMode.RELIABLE_PASTE
        if (len(sys.argv) > 1 and sys.argv[1].lower().startswith("rel"))
        else TextInsertionMode.STANDARD
    )

    app = QApplication(sys.argv[:1])
    window = QWidget()
    window.setWindowTitle("Fluentry injection self-test")
    window.resize(720, 220)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel(f"mode: {mode.value} — do not type here"))
    field = QPlainTextEdit()
    layout.addWidget(field)
    window.show()
    window.raise_()
    window.activateWindow()
    field.setFocus(Qt.FocusReason.OtherFocusReason)

    overlay = None
    if len(sys.argv) > 2 and sys.argv[2] == "with-overlay":
        # The real flow inserts while the recording overlay is still up.
        from fluentry.persistence.settings_store import SettingsStore
        from fluentry.persistence.defaults import FileDefaults
        from fluentry.ui.overlay import OverlayMode, RecordingOverlay
        from fluentry.ui.theme import Palette

        overlay = RecordingOverlay(Palette(is_dark=True, accent="#e95420"),
                                   SettingsStore(defaults=FileDefaults()))
        overlay.present(OverlayMode.TRANSCRIBING)
        print("overlay presented")
        if len(sys.argv) > 3 and sys.argv[3] == "dismiss-first":
            # What the fix would do: put the overlay away before inserting,
            # so focus is back on the target window.
            QTimer.singleShot(1200, overlay.dismiss)
            print("overlay will be dismissed before insertion")

    backend = make_injection_backend()
    clipboard = make_clipboard()
    service = TypingService(backend=backend, clipboard=clipboard, insertion_mode=mode)

    result: dict = {}

    def run() -> None:
        # Never inject before the field really has focus: a compositor may
        # refuse to focus a newly mapped window, and injecting then measures
        # nothing but where the focus happened to be.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not field.hasFocus():
            time.sleep(0.1)
        result["focused"] = bool(field.hasFocus())
        result["waited"] = round(10 - (deadline - time.monotonic()), 1)
        if not result["focused"]:
            result["returned"] = "SKIPPED - never got focus"
            return
        started = time.monotonic()
        try:
            result["returned"] = service.type_text(SAMPLE, mode=mode)
        except Exception as error:
            import traceback
            result["error"] = traceback.format_exc()
            result["returned"] = f"RAISED {type(error).__name__}: {error}"
        result["seconds"] = time.monotonic() - started
        time.sleep(1.0)

    def finish() -> None:
        got = field.toPlainText()
        print(f"backend        : {backend.name}")
        print(f"mode           : {mode.value}")
        print(f"field focused  : {result.get('focused')}")
        active = QApplication.activeWindow()
        print(f"active window  : {active.windowTitle() if active else None}")
        focus_widget = QApplication.focusWidget()
        print(f"focus widget   : {type(focus_widget).__name__ if focus_widget else None}")
        if result.get("error"):
            print("--- exception ---")
            print(result["error"])
        print(f"type_text ->   : {result.get('returned')} in {result.get('seconds', 0):.2f}s")
        print(f"expected       : {SAMPLE!r}")
        print(f"actually got   : {got!r}")
        if got == SAMPLE:
            print("RESULT: PASS")
        elif got:
            print(f"RESULT: PARTIAL — {len(got)} of {len(SAMPLE)} characters")
        else:
            print("RESULT: FAIL — nothing arrived")
        app.quit()

    worker = threading.Thread(target=run, daemon=True)

    def start() -> None:
        worker.start()
        QTimer.singleShot(20000, finish)

    QTimer.singleShot(400, start)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
