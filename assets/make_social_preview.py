"""Render the 1280x640 social preview card (GitHub social preview / README hero).

Deterministic and font-driven rather than an AI-generated picture: the card has to be legible at
thumbnail size and the words in it have to be the right words. Run it from anywhere:

    .venv/bin/python assets/make_social_preview.py

Writes assets/social-preview.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG_TOP = (13, 17, 23)
BG_BOTTOM = (17, 24, 36)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
DIM = (110, 118, 129)
ACCENT = (86, 205, 178)
ACCENT_2 = (240, 180, 90)
PANEL = (22, 30, 42)
PANEL_EDGE = (48, 60, 78)

BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
MONO_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    raise SystemExit("no usable font found")


def main() -> int:
    image = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(image)

    # A quiet vertical gradient, so the card does not read as a flat swatch.
    for y in range(H):
        t = y / (H - 1)
        draw.line(
            [(0, y), (W, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)),
        )

    title = font(BOLD_CANDIDATES, 62)
    subtitle = font(REGULAR_CANDIDATES, 27)
    body = font(REGULAR_CANDIDATES, 25)
    mono = font(MONO_CANDIDATES, 21)
    small = font(REGULAR_CANDIDATES, 22)

    x = 64

    # Title + subtitle
    draw.text((x, 56), "jev-ultrafast-mcp", font=title, fill=FG)
    draw.text((x, 136), "Give your AI agent a browser it can actually drive",
              font=subtitle, fill=ACCENT)

    # The element table, which is the whole idea: controls as a readable list.
    panel = (x, 198, W - 64, 402)
    draw.rounded_rectangle(panel, radius=14, fill=PANEL, outline=PANEL_EDGE, width=2)

    rows = [
        ("e6", "cmb", "Passengers \u25b8 1 adult", "opts{1 adult | 2 adults | 3 adults}"),
        ("e7", "chk", "Nonstop only", "\u2713"),
        ("e8", "btn", "Search", ""),
    ]
    line_y = 222
    for ref, role, name, tail in rows:
        draw.text((x + 26, line_y), ref, font=mono, fill=ACCENT)
        draw.text((x + 78, line_y), role, font=mono, fill=ACCENT_2)
        draw.text((x + 148, line_y), name, font=mono, fill=FG)
        if tail:
            draw.text((x + 148 + mono.getlength(name) + 14, line_y), tail, font=mono, fill=DIM)
        line_y += 38

    draw.text((x + 26, line_y + 4),
              "refs are stable  \u00b7  a stale one is refused with a reason, not clicked",
              font=mono, fill=DIM)

    # What it buys you, then what it is.
    draw.text((x, 434),
              "batched actions  \u00b7  delta observations  \u00b7  deterministic asserts",
              font=body, fill=FG)
    draw.text((x, 472),
              "macro replay at zero model cost  \u00b7  no screenshots in the loop",
              font=body, fill=FG)

    draw.line([(x, 528), (W - 64, 528)], fill=PANEL_EDGE, width=2)
    draw.text((x, 552), "MCP  \u00b7  Chrome DevTools Protocol  \u00b7  no Playwright  \u00b7  MIT",
              font=small, fill=MUTED)
    url = "github.com/jiawei686/jev-ultrafast-mcp"
    draw.text((W - 64 - small.getlength(url), 552), url, font=small, fill=ACCENT)

    out = Path(__file__).with_name("social-preview.png")
    image.save(out, optimize=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {W}x{H})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
