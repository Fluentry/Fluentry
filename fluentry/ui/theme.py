"""Colours and stylesheet.

The palette follows **Adwaita**, GNOME's design language, rather than the
macOS greys the original used, so Fluentry sits alongside Ubuntu's own
apps instead of looking imported: Adwaita's window/view/card greys, Yaru
accent colours, the Ubuntu font stack, switch-style toggles and a header
bar at the top of each window.

"System" follows the desktop: Qt reports the platform palette, and a dark
window background means a dark desktop, which is how GNOME, KDE and the
freedesktop colour-scheme portal all present themselves to a Qt client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..persistence.settings_types import AccentColorOption, ThemePreference


@dataclass(frozen=True)
class Palette:
    is_dark: bool
    accent: str

    #: Adwaita's named colours. Keeping the real values means a screenshot
    #: of Fluentry next to GNOME Settings shows the same greys.
    @property
    def window(self) -> str:
        return "#242424" if self.is_dark else "#fafafa"

    @property
    def surface(self) -> str:
        """Adwaita "card" / "view": the raised sheet content sits on."""
        return "#303030" if self.is_dark else "#ffffff"

    @property
    def surface_raised(self) -> str:
        return "#3a3a3a" if self.is_dark else "#ebebeb"

    @property
    def headerbar(self) -> str:
        return "#303030" if self.is_dark else "#ebebeb"

    @property
    def sidebar(self) -> str:
        return "#2a2a2a" if self.is_dark else "#f2f2f2"

    @property
    def text(self) -> str:
        return "#ffffff" if self.is_dark else "#2e3436"

    @property
    def secondary_text(self) -> str:
        return "#b0aeab" if self.is_dark else "#5e5c64"

    @property
    def separator(self) -> str:
        return "#454545" if self.is_dark else "#d8d4d0"

    @property
    def hover(self) -> str:
        """Adwaita hovers by lightening, not by drawing a border."""
        return "#404040" if self.is_dark else "#e4e2e0"

    @property
    def selected(self) -> str:
        """Sidebar selection: a quiet fill, never a saturated accent block."""
        return "#3a3a3a" if self.is_dark else "#dcdad6"

    @property
    def on_accent(self) -> str:
        """Yaru accents are dark enough to carry white text."""
        return "#ffffff"

    @property
    def danger(self) -> str:
        return "#c01c28"

    @property
    def success(self) -> str:
        return "#2ec27e"


def resolve_is_dark(preference: ThemePreference, system_is_dark: bool) -> bool:
    if preference is ThemePreference.DARK:
        return True
    if preference is ThemePreference.LIGHT:
        return False
    return system_is_dark


PORTAL_ARGS = [
    "gdbus",
    "call",
    "--session",
    "--dest",
    "org.freedesktop.portal.Desktop",
    "--object-path",
    "/org/freedesktop/portal/desktop",
    "--method",
    "org.freedesktop.portal.Settings.ReadOne",
    "org.freedesktop.appearance",
]

#: GNOME's named accents, in Yaru's shades.
GNOME_ACCENT_HEXES = {
    "blue": "#3584E4",
    "teal": "#2190A4",
    "green": "#3A944A",
    "yellow": "#C88800",
    "orange": "#ED5B00",
    "red": "#E62D42",
    "pink": "#D56199",
    "purple": "#9141AC",
    "slate": "#6F8396",
}


def _read_portal(key: str) -> str | None:
    """Ask the desktop, through the setting portal every compositor exposes."""
    import subprocess

    try:
        result = subprocess.run(
            [*PORTAL_ARGS, key], capture_output=True, text=True, timeout=1.5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _read_gsetting(key: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", key],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip().strip("'") if result.returncode == 0 else None


@lru_cache(maxsize=1)
def system_colour_scheme() -> str | None:
    """"dark", "light", or None when the desktop has no preference."""
    raw = _read_portal("color-scheme")
    if raw is not None:
        # Portal: 1 prefers dark, 2 prefers light, 0 means no preference.
        if "uint32 1" in raw:
            return "dark"
        if "uint32 2" in raw:
            return "light"
        if "uint32 0" in raw:
            return None
    scheme = _read_gsetting("color-scheme")
    if scheme == "prefer-dark":
        return "dark"
    if scheme == "prefer-light":
        return "light"
    return None


@lru_cache(maxsize=1)
def system_accent() -> str | None:
    """The accent the user picked in their desktop settings, as hex."""
    raw = _read_portal("accent-color")
    if raw:
        numbers = re.findall(r"[0-9]*\.?[0-9]+", raw)
        if len(numbers) >= 3:
            red, green, blue = (float(value) for value in numbers[:3])
            if max(red, green, blue) <= 1.0:
                return "#%02X%02X%02X" % tuple(round(value * 255) for value in (red, green, blue))
    named = _read_gsetting("accent-color")
    return GNOME_ACCENT_HEXES.get(named or "")


@lru_cache(maxsize=1)
def system_font() -> tuple[str, int] | None:
    """The desktop's UI font, as (family, point size)."""
    raw = _read_gsetting("font-name")
    if not raw:
        return None
    match = re.match(r"^(.*?)\s+(\d+)$", raw)
    if not match:
        return (raw, 0)
    return (match.group(1), int(match.group(2)))


def system_is_dark(application=None) -> bool:
    """True when the desktop is using a dark colour scheme.

    The desktop's own answer comes first; the Qt palette is only a fallback
    for sessions that expose neither the portal nor gsettings.
    """
    scheme = system_colour_scheme()
    if scheme is not None:
        return scheme == "dark"
    try:
        from PySide6.QtGui import QGuiApplication, QPalette

        application = application or QGuiApplication.instance()
        if application is None:
            return False
        palette = application.palette()
        window = palette.color(QPalette.ColorRole.Window)
        return window.lightness() < 128
    except Exception:
        return False


def palette_for(
    preference: ThemePreference,
    accent: AccentColorOption,
    application=None,
    accent_is_explicit: bool = True,
) -> Palette:
    """The palette to draw with.

    When the user has not picked an accent, the desktop's own accent wins,
    so Fluentry changes colour along with the rest of their session.
    """
    accent_hex = accent.hex
    if not accent_is_explicit:
        accent_hex = system_accent() or accent_hex
    return Palette(
        is_dark=resolve_is_dark(preference, system_is_dark(application)),
        accent=accent_hex,
    )


#: GNOME ships Ubuntu Sans on Ubuntu and Cantarell elsewhere; the rest are
#: fallbacks so the app never lands on a serif face.
FONT_STACK = '"Ubuntu Sans", "Ubuntu", "Cantarell", "Noto Sans", sans-serif'


def text_scaling_factor() -> float:
    raw = _read_gsetting("text-scaling-factor")
    try:
        value = float(raw) if raw else 1.0
    except ValueError:
        return 1.0
    return value if 0.5 <= value <= 3.0 else 1.0


def apply_system_font(application) -> None:
    """Give Qt the desktop's UI font, at the user's text scale.

    A stylesheet alone leaves Qt laying text out with its own default
    metrics; setting the application font is what makes Fluentry's text
    match the rest of the session, including accessibility scaling.
    """
    from PySide6.QtGui import QFont

    desktop_font = system_font()
    if not desktop_font:
        return
    family, points = desktop_font
    font = QFont(family)
    if points:
        font.setPointSizeF(points * text_scaling_factor())
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    application.setFont(font)


def stylesheet(palette: Palette) -> str:
    """One stylesheet for the whole app, so every window matches.

    The font follows the desktop's own UI font when it reports one, so
    Fluentry's text matches every other window on screen.
    """
    desktop_font = system_font()
    font_family = f'"{desktop_font[0]}", {FONT_STACK}' if desktop_font else FONT_STACK
    # GNOME states its UI font in points; converting to pixels by hand gets
    # the size wrong on any display that is not exactly 96 dpi.
    base = desktop_font[1] if desktop_font and desktop_font[1] else 11
    font_size = f"{base}pt"
    title_size = f"{base + 7}pt"
    section_size = f"{base}pt"
    hint_size = f"{max(8, base - 1)}pt"
    metric_size = f"{base + 12}pt"
    return f"""
    /* Only the window itself paints a background. Child widgets stay
       transparent so a label never draws the window grey on top of the
       card it is sitting in. */
    QWidget {{
        color: {palette.text};
        font-family: {font_family};
        font-size: {font_size};
    }}
    QMainWindow, QDialog, QWidget#Window {{
        background-color: {palette.window};
    }}
    QStackedWidget, QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}

    /* GNOME titles are heavy and short; body text stays regular weight. */
    QLabel#Title {{ font-size: {title_size}; font-weight: 700; }}
    QLabel#Subtitle {{ color: {palette.secondary_text}; }}
    QLabel#SectionTitle {{ font-size: {section_size}; font-weight: 700; margin-top: 6px; }}
    QLabel#RowTitle {{ font-size: {section_size}; font-weight: 700; }}
    QLabel#Metric {{ font-size: {metric_size}; font-weight: 700; color: {palette.accent}; }}
    QLabel#Hint {{ color: {palette.secondary_text}; font-size: {hint_size}; }}

    /* Adwaita header bar: flat, separated by a hairline, never a gradient. */
    QWidget#HeaderBar {{
        background-color: {palette.headerbar};
        border-bottom: 1px solid {palette.separator};
    }}
    QWidget#SidebarHeader {{
        background-color: {palette.sidebar};
        border-bottom: 1px solid {palette.separator};
        border-right: 1px solid {palette.separator};
    }}
    QLabel#HeaderTitle {{ font-size: {section_size}; font-weight: 700; }}
    QLabel#HeaderSubtitle {{ font-size: {hint_size}; color: {palette.secondary_text}; }}

    /* Adwaita "boxed list": one rounded card holding stacked rows. */
    QFrame#Card {{
        background-color: {palette.surface};
        border: 1px solid {palette.separator};
        border-radius: 12px;
    }}

    QPushButton {{
        background-color: {palette.surface_raised};
        border: 1px solid {palette.separator};
        border-radius: 6px;
        padding: 6px 16px;
        min-height: 20px;
    }}
    QPushButton:hover {{ background-color: {palette.hover}; }}
    QPushButton:pressed {{ background-color: {palette.selected}; }}
    QPushButton:disabled {{ color: {palette.secondary_text}; }}
    /* Adwaita "suggested-action". */
    QPushButton#Primary {{
        background-color: {palette.accent};
        border: 1px solid {palette.accent};
        color: {palette.on_accent};
        font-weight: 700;
    }}
    QPushButton#Primary:hover {{ background-color: {palette.accent}; }}
    QPushButton#Primary:disabled {{
        background-color: {palette.surface_raised};
        border-color: {palette.separator};
        color: {palette.secondary_text};
    }}
    /* Adwaita "destructive-action". */
    QPushButton#Destructive {{
        background-color: {palette.danger};
        border: 1px solid {palette.danger};
        color: #ffffff;
    }}
    /* Header-bar buttons are flat until you touch them. */
    QWidget#HeaderBar QPushButton {{
        background-color: transparent;
        border: 1px solid transparent;
    }}
    QWidget#HeaderBar QPushButton:hover {{ background-color: {palette.hover}; }}

    QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit, QTextEdit, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {palette.surface};
        border: 1px solid {palette.separator};
        border-radius: 6px;
        padding: 5px;
        selection-background-color: {palette.accent};
        selection-color: {palette.on_accent};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 2px solid {palette.accent};
        padding: 4px;
    }}

    /* Adwaita sidebar: quiet selection, readable text, no accent block. */
    QListWidget#Sidebar {{
        background-color: {palette.sidebar};
        border: none;
        border-right: 1px solid {palette.separator};
        padding: 6px;
    }}
    QListWidget#Sidebar::item {{ padding: 9px 10px; border-radius: 6px; }}
    QListWidget#Sidebar::item:hover {{ background-color: {palette.hover}; }}
    QListWidget#Sidebar::item:selected {{
        background-color: {palette.selected};
        color: {palette.text};
        font-weight: 700;
    }}

    /* GNOME settings rows use switches, not tick boxes. */
    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{
        width: 44px;
        height: 24px;
        border-radius: 12px;
        border: 1px solid {palette.separator};
        background-color: {palette.surface_raised};
    }}
    QCheckBox::indicator:checked {{
        background-color: {palette.accent};
        border-color: {palette.accent};
        image: none;
    }}
    QCheckBox::indicator:disabled {{ background-color: {palette.separator}; }}

    QRadioButton {{ spacing: 10px; }}
    QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 9px;
        border: 1px solid {palette.separator};
        background-color: {palette.surface};
    }}
    QRadioButton::indicator:checked {{
        background-color: {palette.accent};
        border: 5px solid {palette.accent};
    }}

    QGroupBox {{
        border: 1px solid {palette.separator};
        border-radius: 12px;
        margin-top: 14px;
        padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
        color: {palette.secondary_text};
    }}

    QScrollArea {{ border: none; }}
    /* Thin, unobtrusive scrollbars, the way GNOME draws them. */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {palette.separator};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {palette.secondary_text}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    QSlider::groove:horizontal {{
        height: 4px; background: {palette.separator}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 18px; height: 18px; margin: -7px 0;
        border-radius: 9px;
        background: {palette.surface};
        border: 1px solid {palette.separator};
    }}
    QSlider::sub-page:horizontal {{ background: {palette.accent}; border-radius: 2px; }}

    QProgressBar {{
        border: none;
        background-color: {palette.separator};
        border-radius: 3px;
        height: 6px;
    }}
    QProgressBar::chunk {{ background-color: {palette.accent}; border-radius: 3px; }}

    QToolTip {{
        background-color: {palette.surface_raised};
        color: {palette.text};
        border: 1px solid {palette.separator};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    """
