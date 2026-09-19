"""Render the extension's toolbar icons.

Same approach as `assets/make_social_preview.py`: deterministic and drawn from the project's own
palette rather than generated, because an icon has to survive being 16 pixels wide. The glyph is a
miniature of the thing the extension shows — a ref, a role, a name, three times over — so the icon
and the popup agree about what this is.

Everything is drawn at 8x and downsampled, which is the only reliable way to get clean edges at
16px. Run it from anywhere:

    .venv/bin/python chrome-extension/icons/make_icons.py

Writes icon16.png, icon32.png, icon48.png and icon128.png next to itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

# The palette from assets/make_social_preview.py, so the icon is not a new visual language.
BG_TOP = (13, 17, 23)
BG_BOTTOM = (19, 27, 40)
EDGE = (48, 60, 78)
ACCENT = (86, 205, 178)      # refs
ACCENT_2 = (240, 180, 90)    # roles
FG = (230, 237, 243)         # names

SIZES = (16, 32, 48, 128)
SUPERSAMPLE = 8

# Geometry in the 128px reference frame; every other size is this scaled.
REF = 128
PAD = 14
RADIUS = 26
BAR_H = 11
BAR_RADIUS = 3
ROWS = 3
ROW_GAP = 13

# (x, width) per column, and the name column's width per row so the rows are not identical.
COL_REF = (26, 11)
COL_ROLE = (45, 19)
COL_NAME_X = 72
NAME_WIDTHS = (30, 40, 24)


def render(size: int) -> Image.Image:
    """Draw one icon at `size`, supersampled then reduced."""
    canvas = REF * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def s(value: float) -> float:
        return value * canvas / REF

    # The plate: a vertical gradient inside a rounded square, with the panel edge from the card.
    plate = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    plate_draw = ImageDraw.Draw(plate)
    for y in range(canvas):
        t = y / (canvas - 1)
        fill = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,)
        plate_draw.line([(0, y), (canvas, y)], fill=fill)
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [s(PAD), s(PAD), s(REF - PAD), s(REF - PAD)], radius=s(RADIUS), fill=255)
    image.paste(plate, (0, 0), mask)
    draw.rounded_rectangle(
        [s(PAD), s(PAD), s(REF - PAD), s(REF - PAD)],
        radius=s(RADIUS), outline=EDGE + (255,), width=max(1, round(s(2))))

    # Three rows of ref / role / name, which is the table in miniature.
    block = ROWS * BAR_H + (ROWS - 1) * ROW_GAP
    top = (REF - block) / 2
    for row in range(ROWS):
        y = top + row * (BAR_H + ROW_GAP)
        for (x, width), colour in (
            (COL_REF, ACCENT),
            (COL_ROLE, ACCENT_2),
            ((COL_NAME_X, NAME_WIDTHS[row]), FG),
        ):
            draw.rounded_rectangle(
                [s(x), s(y), s(x + width), s(y + BAR_H)],
                radius=s(BAR_RADIUS), fill=colour + (255,))

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    here = Path(__file__).resolve().parent
    written = []
    for size in SIZES:
        out = here / f"icon{size}.png"
        render(size).save(out, optimize=True)
        written.append(f"{out.name} ({out.stat().st_size} B)")
    print("wrote " + ", ".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
