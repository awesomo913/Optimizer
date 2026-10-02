"""Generate docs/assets/cost-compare.png — yearly-cost bar chart for the README.

Compares Local Device Optimizer (free, local, unlimited) against the cheapest
current list price of a few well-known paid "PC optimizer" suites. Prices
were looked up on each vendor's official pricing page on 2026-09-30 — see
the table in README.md for the exact plan names, prices, and source URLs.
This script is a one-off content-generation tool, not part of the app
itself, so its dependencies (matplotlib) are intentionally not in
requirements.txt:

    uv pip install matplotlib
    python scripts/make_cost_chart.py

Output: docs/assets/cost-compare.png
"""
from __future__ import annotations

import os

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "..", "docs", "assets", "cost-compare.png")

# (label, yearly cost in USD, note). See README.md "Comparison" section for
# exact plan names, prices, source URLs, and the date each page was checked
# (2026-09-30).
DATA = [
    ("CCleaner  ·  Professional", 44.95, "list price, 1 device/yr"),
    ("IObit  ·  Advanced SystemCare Pro", 29.99, "list price, 1 PC/yr"),
    ("Ashampoo  ·  WinOptimizer Pro 29", 14, "from, 1 PC/yr"),
    ("Razer Cortex", 0, "free"),
    ("Windows Task Manager", 0, "free, built-in"),
    ("Local Device Optimizer", 0, "free, forever"),
]

# System-monitor identity: near-black green-tinted bg, phosphor green, amber.
BG = "#050b07"
FG = "#eafff2"
MUTED = "#9fd9b6"
FREE_COLOR = "#39ff88"
PAID_COLOR = "#b87a14"
GRID = "#143a24"  # faint phosphor grid, pre-blended over BG (opaque so label boxes hide it)

plt.rcParams["font.family"] = ["Consolas", "DejaVu Sans Mono", "monospace"]


def main() -> None:
    labels = [d[0] for d in DATA]
    values = [d[1] for d in DATA]
    colors = [FREE_COLOR if v == 0 else PAID_COLOR for v in values]
    top = max(values)

    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=200)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    ypos = list(range(len(DATA)))[::-1]
    # A sliver so the $0 rows still show a visible teal marker.
    shown = [v if v > 0 else top * 0.012 for v in values]
    ax.barh(ypos, shown, color=colors, height=0.62, zorder=3)

    label_bbox = dict(boxstyle="square,pad=0.15", fc=BG, ec="none")
    label_texts = []
    for y, (_, value, note) in zip(ypos, DATA, strict=True):
        price = "$0" if value == 0 else f"${value:g}/yr"
        color = FREE_COLOR if value == 0 else FG
        x = max(value, top * 0.012) + top * 0.015
        label_texts.append(ax.text(x, y, price, va="center", ha="left", color=color,
                fontsize=13, fontweight="bold", zorder=4, bbox=label_bbox))
        label_texts.append(ax.text(x, y - 0.33, note, va="center", ha="left", color=MUTED,
                fontsize=8.5, zorder=4, bbox=label_bbox))

    ax.set_yticks(ypos, labels)
    ax.tick_params(axis="y", colors=FG, labelsize=11, length=0)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9)
    ax.set_xlim(0, top * 1.3)
    ax.set_xlabel("USD per year (official list price) — checked 2026-09-30",
                  color=MUTED, fontsize=8.5)
    ax.set_title("What a year of \"PC optimizer\" software costs", color=FG, fontsize=14,
                 pad=14, loc="left", fontweight="bold")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=GRID, linewidth=0.5, zorder=0, alpha=0.6)

    fig.tight_layout()

    # Extend the x-axis so the widest label (sub-label text, which can run
    # past the bar) ends with real padding before the image's right edge,
    # instead of running flush against it. Measured in actual rendered
    # pixels so it holds regardless of font/DPI/label-length changes.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    axis_width_px = ax.get_window_extent(renderer=renderer).width
    left, right = ax.get_xlim()
    max_text_x_data = max(
        ax.transData.inverted().transform((t.get_window_extent(renderer=renderer).x1, 0))[0]
        for t in label_texts
    )
    pad_px = 40
    new_right = left + (max_text_x_data - left) / (1 - pad_px / axis_width_px)
    ax.set_xlim(left, max(right, new_right))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    fig.savefig(OUT_PATH, facecolor=BG)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
