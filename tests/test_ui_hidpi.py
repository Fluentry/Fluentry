"""Rendering on a scaled display.

A 2x display is where pixel-ratio mistakes show up: a pixmap that forgets
its ratio is drawn at twice the size it should be, so only its top-left
quarter lands inside the widget. Everything here runs in a Qt application
started at `QT_SCALE_FACTOR=2`, which is why it is a separate module —
the scale has to be set before the first QApplication exists.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="the GUI extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = "2"

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fluentry.persistence.settings_types import (  # noqa: E402
    AccentColorOption,
    ThemePreference,
)
from fluentry.ui.icons import configure_icon_theme, themed_icon, tinted_pixmap  # noqa: E402
from fluentry.ui.theme import palette_for, stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def scaled_app():
    application = QApplication.instance()
    if application is None:
        application = QApplication(["fluentry-hidpi-tests"])
    if application.primaryScreen().devicePixelRatio() < 2:
        pytest.skip("this process was already running at 1x")
    configure_icon_theme()
    return application


@pytest.fixture
def app_state(settings):
    from fluentry.app import AppState
    from fluentry.persistence.history_store import TranscriptionHistoryStore

    return AppState(
        settings=settings,
        history=TranscriptionHistoryStore(load=False),
        start_services=False,
    )


def test_the_display_really_is_scaled(scaled_app):
    assert scaled_app.primaryScreen().devicePixelRatio() == 2.0


def test_tinting_keeps_the_icon_the_same_size_on_screen(scaled_app):
    source = QPixmap(32, 32)
    source.setDevicePixelRatio(2.0)
    source.fill()

    tinted = tinted_pixmap(source, "#00ff00")

    assert tinted.devicePixelRatio() == 2.0
    assert tinted.deviceIndependentSize() == source.deviceIndependentSize()


def test_a_themed_icon_asked_for_at_logical_size_stays_that_size(scaled_app):
    """Multiplying by the ratio here would scale the icon a second time."""
    pixmap = themed_icon("object-select").pixmap(16, 16)
    assert pixmap.deviceIndependentSize().width() == 16
    assert pixmap.width() == 32  # Sharper, but still 16 points wide.


def test_status_icons_fit_inside_their_label(scaled_app, app_state):
    """The reported symptom: only a quarter of each icon was visible."""
    from fluentry.ui.pages import WelcomePage

    palette = palette_for(ThemePreference.DARK, AccentColorOption.BLUE)
    scaled_app.setStyleSheet(stylesheet(palette))
    page = WelcomePage(app_state, palette)
    page.resize(600, 520)
    page.show()
    page.refresh()
    scaled_app.processEvents()

    assert page.status_rows
    for row in page.status_rows:
        size = row.icon.pixmap().deviceIndependentSize()
        assert size.width() <= row.icon.width(), f"{row.title.text()}: icon too wide"
        assert size.height() <= row.icon.height(), f"{row.title.text()}: icon too tall"
    scaled_app.setStyleSheet("")


def test_status_icons_still_line_up_when_scaled(scaled_app, app_state):
    from fluentry.ui.pages import WelcomePage

    palette = palette_for(ThemePreference.DARK, AccentColorOption.BLUE)
    scaled_app.setStyleSheet(stylesheet(palette))
    page = WelcomePage(app_state, palette)
    page.resize(600, 520)
    page.show()
    page.refresh()
    scaled_app.processEvents()

    for row in page.status_rows:
        icon_centre = row.icon.mapTo(row, row.icon.rect().center()).y()
        title_centre = row.title.mapTo(row, row.title.rect().center()).y()
        assert abs(icon_centre - title_centre) <= 1
    scaled_app.setStyleSheet("")


def test_the_sidebar_icons_are_sharp_rather_than_stretched(scaled_app, app_state):
    from fluentry.ui.main_window import MainWindow

    window = MainWindow(app_state, palette_for(ThemePreference.DARK, AccentColorOption.BLUE))
    window.show()
    scaled_app.processEvents()

    for row in range(window.sidebar.count()):
        icon = window.sidebar.item(row).icon()
        assert not icon.isNull()
        pixmap = icon.pixmap(window.sidebar.iconSize())
        assert pixmap.deviceIndependentSize().width() <= 16
        # Qt should have handed back the 2x rendering of the glyph.
        assert pixmap.devicePixelRatio() == 2.0
