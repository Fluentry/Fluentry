"""System tray icon and menu.

A freedesktop tray icon (StatusNotifierItem on KDE and
GNOME-with-AppIndicator, legacy XEmbed elsewhere) — Qt picks whichever
the desktop provides.

The tray shows the app's own mark. Recording state is a dot drawn over
it rather than a colour change, because the mark is a fixed-colour brand
asset — and a dot reads at 22px where a hue shift does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..i18n import tr

ICON_SIZE = 64


def window_icon(accent: str) -> QIcon:
    """The icon a window and the switcher show."""
    from ..resources import app_icon, exists

    return app_icon() if exists("icon-64.png") else draw_microphone_icon(accent)


def tray_icon(is_recording: bool, accent: str) -> QIcon:
    """The mark, with a dot over it while a dictation is running."""
    from ..resources import app_icon, exists, path

    if not exists("icon-64.png"):
        # No artwork installed: fall back to the drawn glyph.
        return draw_microphone_icon(accent)
    if not is_recording:
        return app_icon()

    icon = QIcon()
    for size in (22, 32, 48, 64, 128):
        file = path(f"icon-{size}.png")
        if not file.is_file():
            continue
        pixmap = QPixmap(str(file))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        diameter = max(6, round(pixmap.width() * 0.42))
        painter.drawEllipse(
            pixmap.width() - diameter, pixmap.height() - diameter, diameter, diameter
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def draw_microphone_icon(colour: str, size: int = ICON_SIZE) -> QIcon:
    """A simple microphone glyph, tinted to show state."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))

    unit = size / 64.0
    capsule = QRectF(24 * unit, 10 * unit, 16 * unit, 28 * unit)
    painter.drawRoundedRect(capsule, 8 * unit, 8 * unit)

    from PySide6.QtGui import QPen

    pen = QPen(QColor(colour))
    pen.setWidthF(4 * unit)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arc = QRectF(17 * unit, 20 * unit, 30 * unit, 28 * unit)
    painter.drawArc(arc, 180 * 16, 180 * 16)
    painter.drawLine(int(32 * unit), int(44 * unit), int(32 * unit), int(54 * unit))
    painter.end()
    return QIcon(pixmap)


@dataclass
class TrayActions:
    toggle_dictation: Callable[[], None]
    open_main_window: Callable[[], None]
    open_settings: Callable[[], None]
    open_history: Callable[[], None]
    quit: Callable[[], None]


class TrayController:
    def __init__(self, palette, actions: TrayActions, parent=None) -> None:
        self._palette = palette
        self._actions = actions
        self.tray = QSystemTrayIcon(parent)
        self.tray.setToolTip("Fluentry")

        self._menu = QMenu()
        self._toggle_action = QAction(tr("Start Dictation"), self._menu)
        self._toggle_action.triggered.connect(lambda: actions.toggle_dictation())
        self._menu.addAction(self._toggle_action)
        self._menu.addSeparator()

        open_action = QAction(tr("Open Fluentry"), self._menu)
        open_action.triggered.connect(lambda: actions.open_main_window())
        self._menu.addAction(open_action)

        history_action = QAction(tr("History"), self._menu)
        history_action.triggered.connect(lambda: actions.open_history())
        self._menu.addAction(history_action)

        settings_action = QAction(tr("Settings…"), self._menu)
        settings_action.triggered.connect(lambda: actions.open_settings())
        self._menu.addAction(settings_action)

        self._menu.addSeparator()
        self._status_action = QAction(tr("Idle"), self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()
        quit_action = QAction(tr("Quit Fluentry"), self._menu)
        quit_action.triggered.connect(lambda: actions.quit())
        self._menu.addAction(quit_action)

        self.tray.setContextMenu(self._menu)
        self.tray.activated.connect(self._on_activated)
        self.set_recording(False)

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.set_recording(self._is_recording)

    def set_recording(self, is_recording: bool) -> None:
        self._is_recording = is_recording
        self.tray.setIcon(tray_icon(is_recording, self._palette.accent))
        self._toggle_action.setText(tr("Stop Dictation") if is_recording else tr("Start Dictation"))
        self.tray.setToolTip(tr("Fluentry — recording") if is_recording else "Fluentry")

    def set_status(self, text: str) -> None:
        self._status_action.setText(text)

    def notify(self, title: str, message: str) -> None:
        if self.tray.isVisible():
            self.tray.showMessage(title, message, tray_icon(False, self._palette.accent), 4000)

    def _on_activated(self, reason) -> None:
        # A left click opens the window; the context menu handles the rest.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._actions.open_main_window()
