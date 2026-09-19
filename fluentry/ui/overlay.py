"""The recording overlay.

The small window that shows a dictation is running. There is no notch to
work around; Linux does not, so the top position is simply a floating panel
in the same place. Everything else carries over: four sizes, a live level
meter, a streaming transcription preview, and a spoken-send countdown.

The overlay is a frameless always-on-top window that never takes focus —
stealing focus mid-dictation would send the user's keystrokes to the wrong
place, which is the whole point of the app.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ..persistence.settings_types import OverlayPosition, OverlaySize

#: Bar count and window size for each overlay size.
SIZE_METRICS = {
    OverlaySize.PILL: (14, QSize(190, 44)),
    OverlaySize.SMALL: (22, QSize(280, 56)),
    OverlaySize.MEDIUM: (32, QSize(420, 72)),
    OverlaySize.LARGE: (48, QSize(620, 96)),
}

#: Level meter smoothing, so the bars read as speech rather than noise.
LEVEL_DECAY = 0.82
BAR_SPACING = 3


class OverlayMode(str, Enum):
    DICTATION = "dictation"
    COMMAND = "commandMode"
    REWRITE = "rewriteMode"
    TRANSCRIBING = "transcribing"

    @property
    def caption(self) -> str:
        return {
            OverlayMode.DICTATION: "Listening",
            OverlayMode.COMMAND: "Command",
            OverlayMode.REWRITE: "Edit",
            OverlayMode.TRANSCRIBING: "Transcribing",
        }[self]


class RecordingOverlay(QWidget):
    """Frameless, always-on-top, never focused."""

    cancelled = Signal()

    def __init__(self, palette, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._settings = settings
        self._mode = OverlayMode.DICTATION
        self._levels: list[float] = []
        self._level = 0.0
        self._preview = ""
        self._countdown: float | None = None
        self._is_dismissing = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # Never accept focus: the user is typing into another app.
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps
        self._timer.timeout.connect(self._tick)

        self.apply_size(self._current_size())

    # --- configuration ----------------------------------------------------

    def _current_size(self) -> OverlaySize:
        try:
            return self._settings.overlay_size
        except Exception:
            return OverlaySize.MEDIUM

    def apply_size(self, size: OverlaySize) -> None:
        bar_count, window_size = SIZE_METRICS[size]
        self._bar_count = bar_count
        self._levels = [0.0] * bar_count
        self.setFixedSize(window_size)
        self.reposition()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.update()

    def reposition(self) -> None:
        """Centre horizontally at the configured edge of the active screen."""
        screen = QApplication.screenAt(QPoint(0, 0)) or QApplication.primaryScreen()
        if screen is None:
            return
        area: QRect = screen.availableGeometry()
        x = area.center().x() - self.width() // 2
        try:
            position = self._settings.overlay_position
            offset = int(self._settings.overlay_bottom_offset)
        except Exception:
            position, offset = OverlayPosition.BOTTOM, 50
        if position is OverlayPosition.TOP:
            y = area.top() + max(8, offset // 2)
        else:
            y = area.bottom() - self.height() - max(8, offset)
        self.move(x, y)

    # --- presentation -----------------------------------------------------

    @property
    def is_presented(self) -> bool:
        return self.isVisible() and not self._is_dismissing

    @property
    def is_dismissing(self) -> bool:
        return self._is_dismissing

    def present(self, mode: OverlayMode = OverlayMode.DICTATION) -> None:
        self._is_dismissing = False
        self._mode = mode
        self._preview = ""
        self._countdown = None
        self._levels = [0.0] * self._bar_count
        self.apply_size(self._current_size())
        self.show()
        self.raise_()
        self._timer.start()

    def dismiss(self) -> None:
        """Hide immediately; a restart before the next frame must win."""
        self._is_dismissing = False
        self._timer.stop()
        self.hide()

    def set_mode(self, mode: OverlayMode) -> None:
        self._mode = mode
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def set_preview(self, text: str) -> None:
        self._preview = text
        self.update()

    def set_countdown(self, seconds: float | None) -> None:
        self._countdown = seconds
        self.update()

    def _tick(self) -> None:
        # Shift the history left and append the newest level, so the meter
        # scrolls the way a waveform does.
        if self._levels:
            self._levels = self._levels[1:] + [self._level]
            self._level *= LEVEL_DECAY
        self.update()

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        radius = self.height() / 2
        background = QColor(self._palette.surface)
        background.setAlpha(238)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        painter.fillPath(path, background)
        painter.setPen(QPen(QColor(self._palette.separator), 1))
        painter.drawPath(path)

        accent = QColor(self._palette.accent)
        content = self.rect().adjusted(16, 10, -16, -10)

        caption = self._mode.caption
        if self._countdown is not None:
            caption = f"Sending in {self._countdown:.1f}s"
        font = QFont(self.font())
        font.setPointSizeF(max(9.0, self.height() * 0.16))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.secondary_text))
        metrics = QFontMetrics(font)
        caption_width = metrics.horizontalAdvance(caption) + 10
        painter.drawText(
            QRect(content.left(), content.top(), caption_width, content.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            caption,
        )

        meter = QRect(
            content.left() + caption_width,
            content.top(),
            max(40, content.width() - caption_width),
            content.height(),
        )
        if self._preview:
            meter.setWidth(max(40, meter.width() // 2))
            self._draw_preview(painter, font, meter, content)
        self._draw_meter(painter, meter, accent)

    def _draw_meter(self, painter: QPainter, area: QRect, accent: QColor) -> None:
        if not self._levels:
            return
        bar_width = max(
            2.0, (area.width() - BAR_SPACING * (len(self._levels) - 1)) / len(self._levels)
        )
        centre = area.center().y()
        maximum = area.height() * 0.9
        painter.setPen(Qt.PenStyle.NoPen)
        for index, level in enumerate(self._levels):
            # A square-root curve keeps quiet speech visible without letting
            # loud speech clip the whole meter.
            magnitude = math.sqrt(max(0.0, min(1.0, level)))
            height = max(3.0, magnitude * maximum)
            x = area.left() + index * (bar_width + BAR_SPACING)
            colour = QColor(accent)
            colour.setAlphaF(0.35 + 0.65 * magnitude)
            painter.setBrush(colour)
            painter.drawRoundedRect(
                QRect(int(x), int(centre - height / 2), int(bar_width), int(height)),
                bar_width / 2,
                bar_width / 2,
            )

    def _draw_preview(self, painter: QPainter, font: QFont, meter: QRect, content: QRect) -> None:
        preview_area = QRect(
            meter.right() + 10,
            content.top(),
            content.right() - meter.right() - 10,
            content.height(),
        )
        if preview_area.width() <= 20:
            return
        metrics = QFontMetrics(font)
        painter.setPen(QColor(self._palette.text))
        # Elide from the left so the newest words stay visible.
        elided = metrics.elidedText(
            self._preview, Qt.TextElideMode.ElideLeft, preview_area.width()
        )
        painter.drawText(
            preview_area,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )


def preview_character_limit(settings) -> int:
    try:
        return settings.transcription_preview_char_limit
    except Exception:
        return 150


def truncated_preview(text: str, limit: int) -> str:
    """Keep the tail: the user cares about the words just spoken."""
    if limit <= 0 or len(text) <= limit:
        return text
    return "…" + text[-limit:]
