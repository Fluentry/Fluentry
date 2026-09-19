#!/usr/bin/env python3
"""Render the real UI to PNGs for the website.

Runs the actual widgets on Qt's `offscreen` platform against a throwaway
XDG profile seeded with plausible demo data, so the shots show a lived-in
app without touching the config, history or keyring of whoever runs this.
The model cache is the real one: a screenshot claiming the engine is ready
should only say so when it is.

    python scripts/capture_screenshots.py site/shots

Each screen is captured twice, `-dark` and `-light`, because the site
follows the reader's colour scheme.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Everything below must import *after* the profile is redirected, because the
# settings store resolves its paths at import time.
PROFILE = Path(tempfile.mkdtemp(prefix="fluentry-shots-"))
for variable in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
    directory = PROFILE / variable.split("_")[1].lower()
    directory.mkdir(parents=True, exist_ok=True)
    os.environ[variable] = str(directory)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Render at 2x so the page can serve one file to both ordinary and HiDPI
# displays; the CSS gives every shot its own layout width.
os.environ.setdefault("QT_SCALE_FACTOR", "2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fluentry.app import AppState  # noqa: E402
from fluentry.persistence.settings_types import CustomDictionaryEntry  # noqa: E402
from fluentry.ui.icons import configure_icon_theme  # noqa: E402
from fluentry.ui.main_window import MainWindow  # noqa: E402
from fluentry.ui.navigation import SettingsSection, SidebarItem  # noqa: E402
from fluentry.ui.overlay import OverlayMode, RecordingOverlay  # noqa: E402
from fluentry.ui.settings_window import SettingsWindow  # noqa: E402
from fluentry.ui.theme import Palette, apply_system_font, stylesheet  # noqa: E402

ACCENT = "#e95420"  # Ubuntu orange, the app's own default.
WINDOW_SIZE = (1120, 720)
SETTINGS_SIZE = WINDOW_SIZE  # One aspect ratio, so the gallery does not jump.


# --- demo data ---------------------------------------------------------------

#: (days ago, hour, app, window title, text, ai-enhanced)
DICTATIONS = [
    (0, 9, "Slack", "engineering — #release", "Morning — the 1.6.1 tag is cut and the release notes are up. Shout if anything looks off before I announce it.", True),
    (0, 10, "Visual Studio Code", "history_store.py — Fluentry", "Refactor the today-summary so it is recomputed on write instead of on read, and leave a note about why the clock is never queried here.", False),
    (0, 11, "Firefox", "Pull request #212", "This looks good to me. One thought: the retry path swallows the original error, so a failed write reads as a silent success in the logs.", True),
    (0, 14, "Thunderbird", "Re: conference talk", "Thanks for the invite — I would love to give the talk. Thirty minutes works well, and I can send a title and abstract by the end of next week.", True),
    (0, 16, "Obsidian", "Daily note", "Idea for the docs: a short page on what Wayland actually costs you, written plainly, rather than a support thread people have to dig up.", False),
    (1, 9, "Slack", "design — #general", "Pushed the new overlay sizes. Small sits under the menu bar nicely now and the meter no longer clips at the right edge.", False),
    (1, 13, "Visual Studio Code", "overlay.py — Fluentry", "Draw the preview text to the left of the meter so the bars keep their full width when there is nothing to show yet.", False),
    (1, 15, "Firefox", "Issue #198 — Fluentry", "Confirmed on Hyprland: the focused window is reported correctly, so per-app prompts work there. Adding it to the compatibility table.", True),
    (2, 10, "Thunderbird", "Re: invoice", "Invoice attached for last month. Let me know if you need it split by project and I will send a revised copy today.", True),
    (2, 11, "Visual Studio Code", "test_history_store.py — Fluentry", "Add a test for the DST-shortened day, because the midnight resolver walks forward an hour at a time and that path has never been exercised.", False),
    (2, 17, "Obsidian", "Release checklist", "Before tagging: run the capability check on both a Wayland and an X11 session, and make sure the onboarding flow still shows exactly once.", False),
    (3, 9, "Slack", "engineering — #release", "The Parakeet download is about six hundred and forty megabytes, so the first launch needs a progress bar rather than a spinner.", True),
    (3, 14, "Firefox", "Hacker News", "It runs the model on your own machine, so there is no account and no per-minute bill. Dictation keeps working with the network off.", False),
    (4, 10, "Visual Studio Code", "text_pipeline.py — Fluentry", "The filler-word pass should run before the dictionary so a replacement never has to match around an um.", False),
    (4, 15, "Thunderbird", "Re: hardware budget", "Both machines are fine for this — the model is only a few hundred megabytes and the transcription is comfortably under a second either way.", True),
    (5, 11, "Obsidian", "Meeting notes", "Agreed: ship the local API behind a switch, loopback only, and document the endpoints in the readme rather than a separate wiki.", False),
    (5, 16, "Slack", "design — #general", "Accent colour now follows the desktop unless someone picks one, so the app changes colour along with the rest of the session.", True),
    (6, 10, "Firefox", "Pull request #205", "Nice catch on the keyring fallback. Storing a hash of the endpoint-and-key pair means changing either one lapses the verification, which is what we want.", True),
    (7, 9, "Visual Studio Code", "settings_store.py — Fluentry", "Give the history expiry a day, seven, thirty and ninety day option, and default it to never so nothing disappears by surprise.", False),
    (8, 14, "Thunderbird", "Re: translation help", "If you can send the Portuguese strings I will get them into the next release. The interface is small enough that it should not take long.", True),
    (9, 11, "Slack", "engineering — #release", "Whisper large is the accuracy ceiling but it wants real memory, so the picker should say that out loud next to the size.", False),
    (10, 15, "Obsidian", "Daily note", "Dictating commit messages turns out to be the thing I do most. Short, frequent, and typing them was never the interesting part.", False),
    (11, 10, "Firefox", "Issue #187 — Fluentry", "Reproduced. Under a sparse icon theme the sidebar falls back to the second name, which is why it looked blank on that setup.", True),
    (12, 13, "Visual Studio Code", "tray.py — Fluentry", "The tray icon should change while recording, because the overlay can be switched off and then there is nothing else to show state.", False),
    (13, 9, "Thunderbird", "Re: sponsorship", "Thank you for the offer. The project is GPL and will stay that way, but I am happy to talk about funding the infrastructure.", True),
    (14, 16, "Slack", "design — #general", "Adwaita greys straight from the spec, so a screenshot next to GNOME Settings shows the same colours instead of nearly the same ones.", False),
    (15, 11, "Obsidian", "Roadmap", "Next up: command mode, an edit mode that rewrites the selection, and meeting tools that keep both sides of a conversation apart.", False),
    (17, 10, "Visual Studio Code", "keycodes.py — Fluentry", "Map the right alt key by its evdev code rather than its symbol, so it keeps working on layouts where that key produces something else.", False),
    (19, 14, "Firefox", "Documentation", "Say plainly which parts of Wayland cost you something and how to get each one back, instead of hiding it behind a compatibility badge.", True),
    (21, 9, "Slack", "engineering — #release", "Cutting one point five today. The dictionary import and export is the headline and the onboarding rewrite is the quiet half.", False),
    (22, 15, "Visual Studio Code", "onboarding.py — Fluentry", "The setup flow should pick a language, download an engine and run one real dictation, and then never appear again.", False),
    (23, 10, "Thunderbird", "Re: accessibility", "Good question — the overlay never takes focus, so a screen reader stays on whatever the person was actually writing in.", True),
    (24, 14, "Firefox", "Issue #176 — Fluentry", "That is the group permission. Adding yourself to input and logging back in is the whole fix, and the check now says so directly.", False),
    (25, 11, "Obsidian", "Daily note", "Wrote the privacy section by listing what leaves the machine, which is nothing, rather than by promising to be careful with it.", False),
    (26, 16, "Slack", "design — #general", "Sidebar icons come from the freedesktop naming spec with a fallback each, so a sparse theme degrades instead of going blank.", True),
    (27, 9, "Visual Studio Code", "llm_client.py — Fluentry", "If the provider times out, type the raw transcript and put the error in the history entry rather than dropping the dictation.", False),
    (28, 13, "Thunderbird", "Re: packaging", "A desktop entry and an autostart file are enough. The background flag keeps it in the tray with no window at login.", True),
    (29, 10, "Firefox", "Documentation", "Worth stating the latency plainly: about three hundred milliseconds for a short phrase, on a plain processor, with nothing uploaded.", False),
]

DICTIONARY = [
    (["fluent tree", "fluently", "flu entry"], "Fluentry"),
    (["pipe wire", "pipewire"], "PipeWire"),
    (["way land"], "Wayland"),
    (["ad wheat a", "advita"], "Adwaita"),
    (["sequel light", "sq lite"], "SQLite"),
    (["para keet", "parrot keet"], "Parakeet"),
    (["kay dee ee", "k d e"], "KDE"),
    (["basically", "you know"], ""),
]


def seed(state: AppState) -> None:
    settings = state.settings
    settings.custom_dictionary_entries = [
        CustomDictionaryEntry(triggers=list(triggers), replacement=replacement)
        for triggers, replacement in DICTIONARY
    ]
    # The AI section is off by default; the shot of it should still show what
    # a configured provider looks like, and a local one is the honest example.
    settings.enable_ai_processing = True
    settings.enable_ai_streaming = True
    settings.selected_model = "llama3.1:8b"
    try:
        state.select_provider("ollama")
    except Exception:
        pass

    # Whoever runs this has their own sound cards, and their model names would
    # go out on a public page. Generic ones say the same thing about the UI.
    from fluentry.services.audio_device import AudioDeviceInfo, TransportType

    microphones = [
        AudioDeviceInfo(
            id=index,
            uid=f"demo-{index}",
            name=name,
            has_input=True,
            transport_type=transport,
        )
        for index, (name, transport) in enumerate(
            (
                ("Built-in Audio Analog Stereo", TransportType.BUILT_IN),
                ("USB Condenser Microphone", TransportType.USB),
                ("Wireless Headset", TransportType.BLUETOOTH),
            )
        )
    ]
    # Both the Voice Engine list and the Welcome page's device count.
    state.available_microphones = lambda: microphones
    state.devices.list_input_devices = lambda: microphones

    now = datetime.now(timezone.utc).astimezone()
    for days_ago, hour, app_name, window_title, text, enhanced in reversed(DICTATIONS):
        moment = (now - timedelta(days=days_ago)).replace(
            hour=hour, minute=(len(text) % 50) + 5, second=0, microsecond=0
        )
        words = len(text.split())
        state.history.add_entry(
            raw_text=text,
            processed_text=text,
            app_name=app_name,
            window_title=window_title,
            timestamp=moment.astimezone(timezone.utc),
            was_ai_processed=enhanced,
            processing_model="llama3.1:8b" if enhanced else None,
            transcription_duration_milliseconds=280 + words * 9,
            ai_processing_duration_milliseconds=(620 + words * 12) if enhanced else None,
            ai_tokens_per_second=61.4 if enhanced else None,
        )
    state.history.finish_pending_writes()


# --- capture -----------------------------------------------------------------


def save(widget, path: Path) -> None:
    """Render through an ARGB image so the overlay keeps its transparency."""
    ratio = widget.devicePixelRatioF()
    image = QImage(
        int(widget.width() * ratio), int(widget.height() * ratio), QImage.Format.Format_ARGB32
    )
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))
    print(f"  {path.name}  {image.width()}×{image.height()}")


def apply_qt_palette(qt: QApplication, palette: Palette) -> None:
    """Match Qt's own palette to the theme.

    The stylesheet paints the window and leaves child widgets transparent, so
    the plain `QWidget`s in between fall back to Qt's palette. On a desktop
    that is a GNOME session that palette already follows the theme; on the
    `offscreen` platform it is always the light default, which would leave a
    grey pane behind the dark shots.
    """
    from PySide6.QtGui import QColor, QPalette as QtPalette

    resolved = QtPalette()
    window, text = QColor(palette.window), QColor(palette.text)
    for role, colour in (
        (QtPalette.ColorRole.Window, window),
        (QtPalette.ColorRole.Base, QColor(palette.surface)),
        (QtPalette.ColorRole.AlternateBase, QColor(palette.surface_raised)),
        (QtPalette.ColorRole.Button, QColor(palette.surface_raised)),
        (QtPalette.ColorRole.WindowText, text),
        (QtPalette.ColorRole.Text, text),
        (QtPalette.ColorRole.ButtonText, text),
        (QtPalette.ColorRole.ToolTipBase, QColor(palette.surface)),
        (QtPalette.ColorRole.ToolTipText, text),
        (QtPalette.ColorRole.Highlight, QColor(palette.accent)),
        (QtPalette.ColorRole.HighlightedText, QColor(palette.on_accent)),
        (QtPalette.ColorRole.PlaceholderText, QColor(palette.secondary_text)),
    ):
        resolved.setColor(role, colour)
    qt.setPalette(resolved)


def capture_theme(qt: QApplication, state: AppState, out: Path, is_dark: bool) -> None:
    suffix = "dark" if is_dark else "light"
    palette = Palette(is_dark=is_dark, accent=ACCENT)
    apply_qt_palette(qt, palette)
    qt.setStyleSheet(stylesheet(palette))

    window = MainWindow(state, palette)
    window.resize(*WINDOW_SIZE)
    window.show()
    qt.processEvents()

    pages = {
        "welcome": SidebarItem.WELCOME,
        "voice-engine": SidebarItem.VOICE_ENGINE,
        "history": SidebarItem.HISTORY,
        "dictionary": SidebarItem.CUSTOM_DICTIONARY,
        "stats": SidebarItem.STATS,
        "ai-enhancement": SidebarItem.AI_ENHANCEMENTS,
    }
    for name, item in pages.items():
        window.show_item(item)
        window.refresh_current_page()
        if item is SidebarItem.HISTORY:
            # The splitter's default split is sized for the minimum window;
            # at this width the list would clip its own previews.
            history = window.pages[item]
            splitter = history.entry_list.parentWidget()
            splitter.setSizes([int(WINDOW_SIZE[0] * 0.46), int(WINDOW_SIZE[0] * 0.54)])
            history.entry_list.setCurrentRow(1)
        for _ in range(3):
            qt.processEvents()
        save(window, out / f"{name}-{suffix}.png")

    settings_window = SettingsWindow(state, palette, None)
    settings_window.resize(*SETTINGS_SIZE)
    settings_window.show_section(SettingsSection.DICTATION)
    for _ in range(3):
        qt.processEvents()
    save(settings_window, out / f"settings-{suffix}.png")
    settings_window.close()

    overlay = RecordingOverlay(palette, state.settings)
    overlay.present(OverlayMode.DICTATION)
    # A still frame needs a waveform; the meter is normally driven by the
    # capture thread, 30 times a second.
    overlay._levels = [0.22, 0.55, 0.81, 0.47, 0.93, 0.68, 0.35, 0.74, 0.52, 0.28]
    overlay.set_preview("then ship it")
    qt.processEvents()
    save(overlay, out / f"overlay-{suffix}.png")
    overlay.dismiss()
    window.close()


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site/shots").resolve()
    qt = QApplication(sys.argv[:1])
    qt.setApplicationName("Fluentry")
    configure_icon_theme()
    apply_system_font(qt)

    state = AppState()
    seed(state)

    from fluentry.persistence.keychain import secret_backend_name

    if secret_backend_name() != "Secret Service":
        print(
            "note: secret-tool is not installed here, so the AI Enhancement shot will "
            "say keys live in an encrypted file rather than the Secret Service.\n"
            "      Install libsecret-tools and re-run for the keyring wording.\n"
        )

    try:
        for is_dark in (True, False):
            print(f"{'dark' if is_dark else 'light'}:")
            capture_theme(qt, state, out, is_dark)
    finally:
        state.shutdown()
        shutil.rmtree(PROFILE, ignore_errors=True)
    print(f"\nWrote screenshots to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
