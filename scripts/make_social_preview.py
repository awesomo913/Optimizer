"""Generate docs/assets/social-preview.png (1280x640) in the system-monitor style.

Renders an SVG with headless Chrome (playwright). One-off content tool, not part
of the app:

    uv run --with playwright python scripts/make_social_preview.py
"""
from __future__ import annotations

import math
import os

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "docs", "assets", "social-preview.png")
G, A, R = "#39ff88", "#ffb020", "#ff5a3c"
MONO = "font-family=\"Consolas,'Courier New',monospace\""
SANS = "font-family=\"'Segoe UI',system-ui,sans-serif\""
CX, CY = 300, 320


def pt(a: float, r: float) -> tuple[float, float]:
    return CX + r * math.cos(math.radians(a)), CY + r * math.sin(math.radians(a))


def arc(a0: float, a1: float, r: float, col: str, w: int, op: float = 1) -> str:
    x0, y0 = pt(a0, r)
    x1, y1 = pt(a1, r)
    large = 1 if a1 - a0 > 180 else 0
    return (f'<path d="M{x0:.1f} {y0:.1f} A{r} {r} 0 {large} 1 {x1:.1f} {y1:.1f}" fill="none" '
            f'stroke="{col}" stroke-width="{w}" opacity="{op}"/>')


def build_svg() -> str:
    s = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">
<defs>
<pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" fill="none" stroke="{G}" stroke-opacity="0.07"/></pattern>
<pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="1.5" fill="#000" fill-opacity="0.28"/></pattern>
<radialGradient id="glow" cx="{CX}" cy="{CY}" r="340" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{G}" stop-opacity="0.16"/><stop offset="1" stop-color="{G}" stop-opacity="0"/></radialGradient>
<filter id="blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
</defs>
<rect width="1280" height="640" fill="#050b07"/><rect width="1280" height="640" fill="url(#grid)"/><rect width="1280" height="640" fill="url(#glow)"/>
<circle cx="{CX}" cy="{CY}" r="226" fill="#08130d" stroke="{G}" stroke-opacity="0.35" stroke-width="2"/>
<circle cx="{CX}" cy="{CY}" r="216" fill="none" stroke="{G}" stroke-opacity="0.12"/>''']
    r = 178
    segs = [(150, 290, G), (290, 350, A), (350, 390, R)]
    s.append('<g filter="url(#blur)">' + "".join(arc(a, b, r, c, 18, 0.7) for a, b, c in segs) + "</g>")
    s.append("".join(arc(a, b, r, c, 15) for a, b, c in segs))
    for i in range(41):
        a = 150 + i * 6
        major = i % 5 == 0
        x0, y0 = pt(a, 154)
        x1, y1 = pt(a, 132 if major else 142)
        col = G if a <= 290 else (A if a <= 350 else R)
        s.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="{col}" '
                 f'stroke-width="{4 if major else 2}" stroke-opacity="{0.95 if major else 0.55}"/>')
    nx, ny = pt(232, 150)
    tx, ty = pt(52, 32)
    s.append(f'<line x1="{tx:.1f}" y1="{ty:.1f}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#d9ffe8" stroke-width="6" stroke-linecap="round"/>')
    s.append(f'<circle cx="{CX}" cy="{CY}" r="20" fill="#08130d" stroke="{G}" stroke-width="5"/><circle cx="{CX}" cy="{CY}" r="6" fill="{G}"/>')
    s.append(f'<text x="{CX}" y="{CY+98}" text-anchor="middle" {MONO} font-size="32" font-weight="700" fill="{G}" letter-spacing="7">RAM</text>')
    s.append(f'<text x="{CX-116}" y="{CY+136}" text-anchor="middle" {MONO} font-size="24" fill="{G}" fill-opacity="0.7">LOW</text>')
    s.append(f'<text x="{CX+116}" y="{CY+136}" text-anchor="middle" {MONO} font-size="24" fill="{R}" fill-opacity="0.85">HIGH</text>')
    s.append(f'<text x="600" y="250" {SANS} font-size="76" font-weight="700" fill="#eafff2">Local Device</text>')
    s.append(f'<text x="600" y="334" {SANS} font-size="76" font-weight="700" fill="#eafff2">Optimizer</text>')
    s.append(f'<rect x="600" y="356" width="96" height="5" fill="{G}"/>')
    s.append(f'<text x="600" y="406" {SANS} font-size="30" fill="#9fd9b6">Find out why your PC is slow — and safely</text>')
    s.append(f'<text x="600" y="446" {SANS} font-size="30" fill="#9fd9b6">pause what you don\'t need.</text>')
    s.append(f'<text x="600" y="512" {MONO} font-size="26" fill="{G}" letter-spacing="1">FREE  ·  OPEN SOURCE  ·  OFFLINE-FIRST</text>')
    s.append('<rect width="1280" height="640" fill="url(#scan)"/></svg>')
    return "\n".join(s)


def main() -> None:
    html = f'<body style="margin:0;background:#050b07">{build_svg()}</body>'
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page(viewport={"width": 1280, "height": 640})
        pg.set_content(html)
        pg.wait_for_timeout(300)
        pg.screenshot(path=OUT)
        b.close()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
