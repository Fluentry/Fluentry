#!/usr/bin/env python3
"""A focused text field that reports what gets typed into it.

The other half of the insertion self-test. It runs in its own process so
that the overlay and the insertion happen somewhere else, exactly as they
do when the user is dictating into another application.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

report = Path(sys.argv[1])
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0

app = QApplication(sys.argv[:1])
window = QWidget()
window.setWindowTitle("Fluentry insertion target")
window.resize(760, 200)
layout = QVBoxLayout(window)
layout.addWidget(QLabel("target window — leave this focused"))
field = QPlainTextEdit()
layout.addWidget(field)
window.show()
window.raise_()
window.activateWindow()
field.setFocus(Qt.FocusReason.OtherFocusReason)

state = {"focused_at": None}


def tick() -> None:
    if field.hasFocus() and state["focused_at"] is None:
        state["focused_at"] = time.monotonic()
        report.with_suffix(".ready").write_text("focused\n")


QTimer.singleShot(100, lambda: None)
timer = QTimer()
timer.timeout.connect(tick)
timer.start(100)


def finish() -> None:
    report.write_text(field.toPlainText())
    app.quit()


QTimer.singleShot(int(seconds * 1000), finish)
app.exec()
