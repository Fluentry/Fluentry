"""The app's own artwork.

`logo-mark.png` is the mark on its own, `logo.png` the mark with the
wordmark for light backgrounds, and `logo-light.png` the same for dark
ones. The `icon-*.png` files are the mark squared off at the sizes a
freedesktop desktop asks for. All are generated from the two source
images by `packaging/make_assets.py`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent

#: Sizes shipped for the window and tray icon.
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def path(name: str) -> Path:
    return DIRECTORY / name


def exists(name: str) -> bool:
    return path(name).is_file()


@lru_cache(maxsize=4)
def app_icon():
    """The application icon, carrying every size the desktop may ask for.

    A multi-size icon lets the compositor pick the right one instead of
    rescaling a single bitmap into mush in the tray.
    """
    from PySide6.QtGui import QIcon, QPixmap

    icon = QIcon()
    for size in ICON_SIZES:
        file = path(f"icon-{size}.png")
        if file.is_file():
            icon.addPixmap(QPixmap(str(file)))
    return icon


def wordmark(is_dark: bool) -> Path:
    """The lockup that will actually be legible on this background."""
    name = "logo-light.png" if is_dark else "logo.png"
    return path(name) if exists(name) else path("logo.png")
