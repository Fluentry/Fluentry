#!/usr/bin/env python3
"""Generate every icon the app and the site need from the two source logos.

Run after changing `logo.png` or `logo_image.png`:

    python packaging/make_assets.py

Kept in the repository so the derived files can always be rebuilt rather
than hand-edited and drifting from the originals.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "fluentry" / "resources"
SITE = ROOT / "site"

BRAND = (244, 75, 24)
#: The tray and window icon sizes a freedesktop desktop asks for.
ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


def trimmed(path: Path) -> Image.Image:
    """The image with its transparent margin removed."""
    image = Image.open(path).convert("RGBA")
    box = image.getbbox()
    return image.crop(box) if box else image


def on_square(image: Image.Image, size: int, padding: float = 0.08) -> Image.Image:
    """Centre an image on a transparent square, so icons stay square."""
    inner = round(size * (1 - 2 * padding))
    scaled = image.copy()
    scaled.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(
        scaled,
        ((size - scaled.width) // 2, (size - scaled.height) // 2),
        scaled,
    )
    return canvas


def recoloured_text(image: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """Repaint the wordmark's dark letters, leaving the orange mark alone.

    The source wordmark is near-black, which disappears on a dark
    background; this produces the variant for dark surfaces.
    """
    out = image.copy()
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                continue
            # Dark and unsaturated means it is a letter, not the mark.
            if max(red, green, blue) < 120 and max(red, green, blue) - min(red, green, blue) < 40:
                pixels[x, y] = (*colour, alpha)
    return out


TAGLINE = "Say it once. Fluentry types it."
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/ubuntu/Ubuntu[wdth,wght].ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def social_card(lockup: Image.Image) -> Image.Image:
    """The 1200x630 image a link preview shows."""
    from PIL import ImageDraw, ImageFilter

    width, height = 1200, 630
    card = Image.new("RGB", (width, height), (14, 14, 16))

    # One soft wash of brand colour, blurred rather than stacked, so it
    # reads as a glow instead of a solid shape.
    glow = Image.new("L", (width, height), 0)
    ImageDraw.Draw(glow).ellipse((250, -180, 950, 380), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(130))
    card.paste(Image.new("RGB", (width, height), BRAND), (0, 0), glow)

    art = lockup.copy()
    art.thumbnail((700, 260), Image.LANCZOS)
    card.paste(art, ((width - art.width) // 2, 215 - art.height // 2), art)

    draw = ImageDraw.Draw(card)
    font = _font(40)
    box = draw.textbbox((0, 0), TAGLINE, font=font)
    draw.text(
        ((width - (box[2] - box[0])) // 2, 400),
        TAGLINE,
        font=font,
        fill=(210, 210, 214),
    )

    small = _font(26)
    label = "Dictation for Linux  ·  runs on your own machine"
    box = draw.textbbox((0, 0), label, font=small)
    draw.text(
        ((width - (box[2] - box[0])) // 2, 478), label, font=small, fill=(130, 130, 138)
    )

    draw.rectangle((0, height - 6, width, height), fill=BRAND)
    return card


def main() -> None:
    RESOURCES.mkdir(parents=True, exist_ok=True)
    mark = trimmed(ROOT / "logo_image.png")
    lockup = trimmed(ROOT / "logo.png")

    mark.save(RESOURCES / "logo-mark.png")
    lockup.save(RESOURCES / "logo.png")
    recoloured_text(lockup, (255, 255, 255)).save(RESOURCES / "logo-light.png")

    for size in ICON_SIZES:
        on_square(mark, size).save(RESOURCES / f"icon-{size}.png")

    # The site uses the mark on its own and the two lockups.
    if SITE.is_dir():
        on_square(mark, 512, padding=0.06).save(SITE / "icon.png")
        on_square(mark, 180, padding=0.06).save(SITE / "apple-touch-icon.png")
        mark.save(SITE / "logo-mark.png")
        recoloured_text(lockup, (255, 255, 255)).save(SITE / "logo-light.png")
        lockup.save(SITE / "logo.png")

    if SITE.is_dir():
        social_card(recoloured_text(lockup, (255, 255, 255))).save(SITE / "social.png")

    print(f"mark {mark.size}, lockup {lockup.size}")
    print(f"wrote {len(ICON_SIZES)} icons and 3 logos to {RESOURCES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
