"""Icons from the desktop's own icon theme.

Qt does not pick up the freedesktop icon theme on its own outside KDE: it
starts with an empty theme name and only `:/icons` on its search path, so
every `QIcon.fromTheme` call comes back null and the UI renders as bare
text. Pointing Qt at the XDG icon directories and at the theme the user
actually chose is what makes the app look like the rest of their desktop.

GNOME uses **symbolic** icons in sidebars and header bars — single-colour
glyphs that take the foreground colour — so each name is tried with the
`-symbolic` suffix first.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon

SYMBOLIC_SUFFIX = "-symbolic"

#: Adwaita ships with GNOME and is the last theme that will have a given
#: name, so it backs up whatever the user has chosen.
FALLBACK_THEME = "Adwaita"


def xdg_icon_directories() -> list[str]:
    """Every directory the icon spec says themes can live in."""
    directories: list[str] = []
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    directories.append(str(Path(data_home) / "icons"))
    directories.append(str(Path.home() / ".icons"))
    raw_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for entry in raw_dirs.split(":"):
        if entry:
            directories.append(str(Path(entry) / "icons"))
    directories.append("/usr/share/pixmaps")
    return [path for path in directories if Path(path).is_dir()]


def desktop_icon_theme() -> str | None:
    """The icon theme the user picked in their desktop settings."""
    from .theme import _read_gsetting

    name = _read_gsetting("icon-theme")
    return name or None


def configure_icon_theme() -> None:
    """Teach Qt where the desktop keeps its icons. Call once at startup."""
    # Anything looked up before now resolved against an empty theme.
    themed_icon.cache_clear()
    existing = list(QIcon.themeSearchPaths())
    for path in reversed(xdg_icon_directories()):
        if path not in existing:
            existing.insert(0, path)
    QIcon.setThemeSearchPaths(existing)
    QIcon.setFallbackThemeName(FALLBACK_THEME)

    chosen = desktop_icon_theme()
    if chosen and not QIcon.fromTheme("go-home" + SYMBOLIC_SUFFIX).isNull():
        QIcon.setThemeName(chosen)
        return
    if chosen:
        QIcon.setThemeName(chosen)
        # A theme that cannot supply a basic name is incomplete; Adwaita
        # is already set as the fallback and will fill the gaps.
        if QIcon.fromTheme("go-home" + SYMBOLIC_SUFFIX).isNull():
            QIcon.setThemeName(FALLBACK_THEME)
    else:
        QIcon.setThemeName(FALLBACK_THEME)


@lru_cache(maxsize=256)
def themed_icon(*names: str) -> QIcon:
    """The first of these icons the theme actually has.

    Each name is tried as a symbolic glyph first, which is what GNOME uses
    in sidebars and header bars, then as a full-colour icon.
    """
    for name in names:
        if not name:
            continue
        candidates = (
            (name,) if name.endswith(SYMBOLIC_SUFFIX) else (name + SYMBOLIC_SUFFIX, name)
        )
        for candidate in candidates:
            icon = QIcon.fromTheme(candidate)
            if not icon.isNull():
                return icon
    return QIcon()


def tinted_pixmap(pixmap, colour: str):
    """Recolour a symbolic glyph, keeping it the same size on screen.

    The device pixel ratio has to be carried over. On a 2x display a 16pt
    icon is a 32px pixmap that Qt draws at half size; a copy that forgets
    the ratio is drawn at full size instead, so only its top-left quarter
    lands inside the label.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap

    if pixmap.isNull():
        return pixmap
    tinted = QPixmap(pixmap.size())
    tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
    tinted.fill(Qt.GlobalColor.transparent)

    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(colour))
    painter.end()
    return tinted


def has_icon(*names: str) -> bool:
    return not themed_icon(*names).isNull()
