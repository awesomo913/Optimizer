"""Generate docs/assets/icon-256.png (social-preview.png comes from make_social_preview.py).

One-off content-generation tool, not part of the app itself — its dependency
(Pillow) is intentionally not in requirements.txt:

    uv pip install pillow
    python scripts/make_icon_assets.py
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "docs", "assets")

BG = (18, 22, 31)
PANEL = (27, 34, 48)
FG = (230, 237, 243)
TEAL = (78, 204, 163)
DARK = (16, 20, 24)


def draw_gauge_icon(size: int) -> Image.Image:
    """A gauge/dial glyph — the same mark used in banner.svg, standalone."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    r_outer = size * 0.46
    thickness = size * 0.09

    bbox = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]
    d.ellipse(bbox, fill=BG)

    track_r = r_outer - thickness * 0.9
    track_bbox = [cx - track_r, cy - track_r, cx + track_r, cy + track_r]
    d.arc(track_bbox, start=-220, end=40, fill=(42, 52, 68), width=int(thickness))
    d.arc(track_bbox, start=-220, end=-70, fill=TEAL, width=int(thickness))

    inner_r = track_r - thickness * 0.75
    d.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=DARK)

    needle_angle = math.radians(-115)
    nx = cx + inner_r * 0.85 * math.cos(needle_angle)
    ny = cy + inner_r * 0.85 * math.sin(needle_angle)
    d.line([cx, cy, nx, ny], fill=FG, width=max(2, int(size * 0.035)))
    hub_r = size * 0.035
    d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill=FG)
    return img


def make_icon() -> None:
    size = 256
    img = draw_gauge_icon(size)
    canvas = Image.new("RGB", (size, size), BG)
    canvas.paste(img, (0, 0), img)
    canvas.save(os.path.join(ASSETS, "icon-256.png"))


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    make_icon()
    print("Wrote icon-256.png")


if __name__ == "__main__":
    main()
