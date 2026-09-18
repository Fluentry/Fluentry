"""System tray icon and menu.

A port of `MenuBarManager`. The macOS menu bar becomes a freedesktop tray
icon (StatusNotifierItem on KDE/GNOME-with-AppIndicator, legacy XEmbed
elsewhere) — Qt picks whichever the desktop provides.

The icon is drawn rather than shipped as a bitmap so it stays crisp at any
tray size and can show recording state by colour, the way the macOS template
image did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

ICON_SIZE = 64


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
        self._toggle_action = QAction("Start Dictation", self._menu)
        self._toggle_action.triggered.connect(lambda: actions.toggle_dictation())
        self._menu.addAction(self._toggle_action)
        self._menu.addSeparator()

        open_action = QAction("Open Fluentry", self._menu)
        open_action.triggered.connect(lambda: actions.open_main_window())
        self._menu.addAction(open_action)

        history_action = QAction("History", self._menu)
        history_action.triggered.connect(lambda: actions.open_history())
        self._menu.addAction(history_action)

        settings_action = QAction("Settings…", self._menu)
        settings_action.triggered.connect(lambda: actions.open_settings())
        self._menu.addAction(settings_action)

        self._menu.addSeparator()
        self._status_action = QAction("Idle", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()
        quit_action = QAction("Quit Fluentry", self._menu)
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
        colour = self._palette.accent if is_recording else self._palette.text
        self.tray.setIcon(draw_microphone_icon(colour))
        self._toggle_action.setText("Stop Dictation" if is_recording else "Start Dictation")
        self.tray.setToolTip("Fluentry — recording" if is_recording else "Fluentry")

    def set_status(self, text: str) -> None:
        self._status_action.setText(text)

    def notify(self, title: str, message: str) -> None:
        if self.tray.isVisible():
            self.tray.showMessage(title, message, draw_microphone_icon(self._palette.accent), 4000)

    def _on_activated(self, reason) -> None:
        # A left click opens the window; the context menu handles the rest.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._actions.open_main_window()
