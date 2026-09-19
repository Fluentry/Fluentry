#!/usr/bin/env python3
"""Drive the real dictation delivery path, overlay and all.

Builds the app's own AppState and ASRService, shows the recording overlay
the way a real dictation does, and runs `deliver()`. Whatever it inserts
lands in whichever window has focus — which is the point: run
`selftest_target.py` first and leave it focused.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fluentry.app import AppState  # noqa: E402
from fluentry.services.asr_service import DictationOutcome  # noqa: E402
from fluentry.ui.overlay import OverlayMode, RecordingOverlay  # noqa: E402
from fluentry.ui.theme import Palette  # noqa: E402

SAMPLE = sys.argv[1] if len(sys.argv) > 1 else "the quick brown fox"

app = QApplication(sys.argv[:1])
state = AppState(start_services=False)
palette = Palette(is_dark=True, accent="#e95420")
overlay = RecordingOverlay(palette, state.settings)

# The real application dismisses the overlay when the service says so.
def on_state(name: str) -> None:
    print(f"  state -> {name}", flush=True)
    if name in ("inserting", "idle"):
        overlay.dismiss()


state.add_state_observer(on_state)
state.asr.focus_return_seconds = 0.25

print(f"backend: {state.typing.backend.name}, mode: {state.settings.text_insertion_mode.value}")
show_overlay = "--no-overlay" not in sys.argv
if show_overlay:
    print("presenting the overlay, as a real dictation does")
    overlay.present(OverlayMode.TRANSCRIBING)
else:
    print("running WITHOUT the overlay")


def run() -> None:
    outcome = DictationOutcome()
    outcome.raw_text = SAMPLE
    started = time.monotonic()
    state.asr.deliver(outcome)
    print(f"deliver() finished in {time.monotonic() - started:.2f}s", flush=True)
    app.quit()


# Give the overlay a moment on screen first, like a real transcription.
QTimer.singleShot(1500, run)
app.exec()
