"""Shared widgets.

The small pieces every screen is built from. Kept deliberately plain so
the UI stays readable: a card is a frame, a metric tile is two labels.
"""

from __future__ import annotations

from typing import Callable, Iterable

from ..i18n import tr

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Title")
    return label


def subtitle_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Subtitle")
    label.setWordWrap(True)
    return label


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def brand_lockup(palette, height: int = 22):
    """The logo with the wordmark, in whichever variant will be legible.

    Returns None when the artwork is not installed, so callers can fall
    back to a plain text title.
    """
    from ..resources import wordmark

    source = wordmark(bool(getattr(palette, "is_dark", False)))
    if not source.is_file():
        return None
    pixmap = QPixmap(str(source))
    if pixmap.isNull():
        return None
    label = QLabel()
    label.setPixmap(
        pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    )
    label.setAccessibleName("Fluentry")
    return label


class HeaderBar(QWidget):
    """The strip GNOME apps put their title and primary actions in.

    Qt cannot hand Mutter a client-side-decorated title bar without taking
    over window dragging, so this sits just below the system title bar and
    carries the same content an Adwaita header bar would: a bold title, an
    optional subtitle, and the window's actions pushed to the right.
    """

    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("HeaderBar")
        # A plain QWidget ignores a stylesheet background without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        self._leading = QHBoxLayout()
        self._leading.setContentsMargins(0, 0, 0, 0)
        self._leading.setSpacing(6)
        layout.addLayout(self._leading)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("HeaderTitle")
        titles.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("HeaderSubtitle")
        self.subtitle.setVisible(bool(subtitle))
        titles.addWidget(self.subtitle)
        layout.addLayout(titles)

        layout.addStretch(1)
        self._trailing = QHBoxLayout()
        self._trailing.setContentsMargins(0, 0, 0, 0)
        self._trailing.setSpacing(6)
        layout.addLayout(self._trailing)

    def set_title(self, title: str, subtitle: str = "") -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))

    def add_action(self, widget: QWidget, leading: bool = False) -> QWidget:
        (self._leading if leading else self._trailing).addWidget(widget)
        return widget


class Card(QFrame):
    """A titled container; the building block of every settings page."""

    def __init__(self, title: str | None = None, subtitle: str | None = None) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(10)
        if title:
            self._layout.addWidget(section_label(title))
        if subtitle:
            self._layout.addWidget(hint_label(subtitle))

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_row(self, *widgets: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for widget in widgets:
            layout.addWidget(widget)
        self._layout.addWidget(row)
        return row

    def add_actions(self, *widgets: QWidget) -> QWidget:
        """A row of buttons at their natural width, aligned to the left.

        `add_row` shares the full width between its widgets, which turns a
        lone button into a slab and three buttons into three slabs. Actions
        should be the size of their labels; the trailing stretch keeps them
        that way.
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        self._layout.addWidget(row)
        return row

    def add_labelled(self, text: str, widget: QWidget, hint: str | None = None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text)
        label.setMinimumWidth(190)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        self._layout.addWidget(row)
        if hint:
            self._layout.addWidget(hint_label(hint))
        return row

    def add_row_layout(self, layout) -> None:
        """Nest a caller-built layout, for grids the card cannot guess."""
        self._layout.addLayout(layout)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)


class MetricTile(QFrame):
    def __init__(self, value: str, caption: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Metric")
        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("Subtitle")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """`a` mixed toward `b` by fraction `t` (0.0 keeps a, 1.0 becomes b)."""
    return QColor(
        round(a.red() * (1 - t) + b.red() * t),
        round(a.green() * (1 - t) + b.green() * t),
        round(a.blue() * (1 - t) + b.blue() * t),
    )


class Switch(QAbstractButton):
    """The control GNOME uses for a setting that is on or off.

    Qt has no switch, and a stylesheet cannot draw the knob inside a
    checkbox indicator, so this paints one: a rounded track that takes the
    accent colour when on, and a circle that sits at the matching end.
    """

    TRACK_WIDTH = 44
    TRACK_HEIGHT = 24
    KNOB_MARGIN = 3

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.TRACK_WIDTH, self.TRACK_HEIGHT)
        self._palette = None

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(self.TRACK_WIDTH, self.TRACK_HEIGHT)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        scheme = self.palette()
        accent = scheme.highlight().color()
        track_off = scheme.mid().color()
        knob = scheme.base().color()
        if self._palette is not None:
            accent = QColor(self._palette.accent)
            track_off = QColor(self._palette.surface_raised)
            knob = QColor("#ffffff" if self.isChecked() else self._palette.text)

        # A disabled switch that keeps painting bright accent reads as a
        # live control, which is how "Stream the response" looked switched
        # on while AI cleanup - the thing it belongs to - was off.
        if not self.isEnabled():
            track = accent if self.isChecked() else track_off
            accent = _blend(track, scheme.window().color(), 0.55)
            track_off = accent
            knob = _blend(knob, scheme.window().color(), 0.45)

        radius = self.TRACK_HEIGHT / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent if self.isChecked() else track_off)
        painter.drawRoundedRect(self.rect(), radius, radius)

        diameter = self.TRACK_HEIGHT - 2 * self.KNOB_MARGIN
        left = (
            self.width() - diameter - self.KNOB_MARGIN
            if self.isChecked()
            else self.KNOB_MARGIN
        )
        painter.setBrush(knob)
        painter.drawEllipse(left, self.KNOB_MARGIN, diameter, diameter)
        painter.end()


class ToggleRow(QWidget):
    """A checkbox with an explanation underneath, bound to a setting."""

    toggled = Signal(bool)

    def __init__(self, title: str, hint: str | None = None, checked: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # GNOME puts the label on the left and the switch at the right edge,
        # so a column of settings lines its controls up.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.label = QLabel(title)
        row.addWidget(self.label, 1)
        self.checkbox = Switch(checked)
        self.checkbox.toggled.connect(self.toggled.emit)
        row.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)

        if hint:
            layout.addWidget(hint_label(hint))

    def set_palette(self, palette) -> None:
        self.checkbox.set_palette(palette)

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()


class StatusRow(QWidget):
    """One readiness line: a themed glyph, a title and an explanation.

    Emoji were standing in for icons here, which renders at whatever size
    and colour the emoji font feels like; a symbolic icon takes the text
    colour and matches every other icon in the window.
    """

    ICON_SIZE = 16

    def __init__(self, palette=None) -> None:
        super().__init__()
        self._palette = palette
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.icon = QLabel()
        self.icon.setFixedWidth(self.ICON_SIZE)
        # Centred inside a box the height of the title's line, so the glyph
        # lines up with the words beside it rather than the top of the row.
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title = QLabel()
        # Its own name, so it does not pick up the top margin that spaces
        # section headings apart inside a card.
        self.title.setObjectName("RowTitle")
        text.addWidget(self.title)
        self.detail = hint_label("")
        text.addWidget(self.detail)
        layout.addLayout(text, 1)

    def set_state(self, title: str, ok: bool, detail: str) -> None:
        from .icons import themed_icon

        self.title.setText(title)
        self.detail.setText(detail)
        # The title's height is only known once it has its text and font.
        self.icon.setFixedHeight(max(self.ICON_SIZE, self.title.sizeHint().height()))
        names = (
            ("object-select", "emblem-ok", "checkbox-checked")
            if ok
            else ("dialog-warning", "emblem-important")
        )
        from .icons import tinted_pixmap

        # QIcon.pixmap already accounts for the display scale: ask for the
        # logical size and it returns a sharper pixmap with the ratio set.
        # Multiplying by the ratio here would scale it a second time.
        pixmap = themed_icon(*names).pixmap(self.ICON_SIZE, self.ICON_SIZE)
        if self._palette is not None:
            pixmap = tinted_pixmap(pixmap, self._palette.success if ok else self._palette.danger)
        self.icon.setPixmap(pixmap)

    def set_palette(self, palette) -> None:
        self._palette = palette


def scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setFrameShape(QFrame.Shape.NoFrame)
    return area


def page(title: str, subtitle: str | None = None) -> tuple[QWidget, QVBoxLayout]:
    """A standard page shell: title, optional subtitle, then content."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(14)
    # The window's header bar carries the page title, so the page itself
    # only needs its description.
    if subtitle:
        layout.addWidget(subtitle_label(subtitle))
    return container, layout


def primary_button(text: str, on_click: Callable[[], None] | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        button.clicked.connect(lambda: on_click())
    return button


def button(text: str, on_click: Callable[[], None] | None = None) -> QPushButton:
    control = QPushButton(text)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        control.clicked.connect(lambda: on_click())
    return control


def combo(items: Iterable[tuple[str, object]], current: object = None) -> QComboBox:
    control = QComboBox()
    for label, value in items:
        control.addItem(label, value)
    if current is not None:
        index = control.findData(current)
        if index >= 0:
            control.setCurrentIndex(index)
    return control


def percent_slider(value: float, on_change: Callable[[float], None]) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(int(round(value * 100)))
    slider.valueChanged.connect(lambda raw: on_change(raw / 100.0))
    return slider


class SparklineChart(QWidget):
    """Thirty days of word counts, drawn as bars.

    A tiny custom widget beats a charting dependency here: the data is one
    small list and the drawing is a dozen lines.
    """

    def __init__(self, palette) -> None:
        super().__init__()
        self._palette = palette
        self._values: list[int] = []
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values: list[int]) -> None:
        self._values = list(values)
        self.update()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if not self._values:
            painter.setPen(QColor(self._palette.secondary_text))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), tr("No activity yet"))
            return

        peak = max(self._values) or 1
        spacing = 3
        width = max(2.0, (self.width() - spacing * (len(self._values) - 1)) / len(self._values))
        for index, value in enumerate(self._values):
            fraction = value / peak
            height = max(2.0, fraction * (self.height() - 4))
            colour = QColor(self._palette.accent)
            colour.setAlphaF(0.35 + 0.65 * fraction)
            painter.setBrush(colour)
            x = index * (width + spacing)
            painter.drawRoundedRect(
                int(x), int(self.height() - height), int(width), int(height), 2, 2
            )
