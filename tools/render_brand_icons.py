"""Rasterize the Entrada favicon PNGs from the measured EntradaMark geometry.

Dev-only asset generator (requires Pillow, present in the repo venv; not a
runtime dependency). Regenerates:

  frontend/public/favicon.png          402x402, rounded pale-icy tile,
                                       transparent outside the tile — raster
                                       twin of frontend/public/favicon-v2.svg
                                       (browser tab + og:image/twitter:image)
  frontend/public/apple-touch-icon.png 180x180, full-bleed opaque square —
                                       iOS applies its own squircle mask and
                                       composites transparency on black, so
                                       no baked rounding; the mark is inset
                                       to 80% width to survive corner crop

Geometry is the pixel-measured Entrada "E" from
frontend/src/components/brand/Entrada.tsx (32x22 viewBox, bar height 4.5,
cyan tip 13/32 ~ 40.6%, flush tokens, cyan diagonal top-left/bottom-right).
The pre-2026-08-06 PNGs were screenshot-style exports of the wordmark
letterform (gapped tokens, ~36% tips, white background, baked black border)
and matched neither the measured mark nor the branded tile.

Run from the repo root:  .venv/bin/python tools/render_brand_icons.py
Renders at 8x and downscales with Lanczos for antialiased edges.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"

NAVY = (2, 80, 128)  # --entrada-navy  #025080
CYAN = (102, 197, 255)  # --entrada-bright #66C5FF
TILE = (207, 239, 255)  # --entrada-light #CFEFFF

# EntradaMark rects (32x22 viewBox), translated +5y so the mark sits
# vertically centered in the 32x32 tile: (x, y, w, h, color).
MARK_VB = 32
TILE_VB = 32
TILE_RADIUS = 6
MARK_BARS: tuple[tuple[float, float, float, float, tuple[int, int, int]], ...] = (
    (0, 5, 13, 4.5, CYAN),
    (13, 5, 19, 4.5, NAVY),
    (0, 13.75, 32, 4.5, NAVY),
    (0, 22.5, 19, 4.5, NAVY),
    (19, 22.5, 13, 4.5, CYAN),
)

SUPERSAMPLE = 8


def _draw_bars(draw: ImageDraw.ImageDraw, scale: float, dx: float = 0.0, dy: float = 0.0) -> None:
    for x, y, w, h, color in MARK_BARS:
        draw.rectangle(
            (
                (dx + x * scale),
                (dy + y * scale),
                (dx + (x + w) * scale),
                (dy + (y + h) * scale),
            ),
            fill=color,
        )


def render_favicon(size: int) -> Image.Image:
    """Rounded pale-icy tile with the full-width mark; transparent corners."""
    ss = size * SUPERSAMPLE
    unit = ss / TILE_VB
    art = Image.new("RGB", (ss, ss), TILE)
    _draw_bars(ImageDraw.Draw(art), scale=unit)
    # Clip everything (tile + any sub-unit bar overhang at the rounded
    # corners) to the tile silhouette so the corners stay transparent.
    mask = Image.new("L", (ss, ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, ss - 1, ss - 1), radius=TILE_RADIUS * unit, fill=255
    )
    art.putalpha(mask)
    return art.resize((size, size), Image.Resampling.LANCZOS)


def render_touch_icon(size: int, mark_width_frac: float = 0.8) -> Image.Image:
    """Full-bleed opaque tile; mark inset so iOS squircle crop can't clip it."""
    ss = size * SUPERSAMPLE
    art = Image.new("RGB", (ss, ss), TILE)
    scale = ss * mark_width_frac / MARK_VB
    # MARK_BARS carries the +5y tile offset; recentre the 32x32 tile box.
    offset = (ss - TILE_VB * scale) / 2
    _draw_bars(ImageDraw.Draw(art), scale=scale, dx=offset, dy=offset)
    return art.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    favicon = render_favicon(402)
    favicon.save(PUBLIC_DIR / "favicon.png")
    print(f"wrote {PUBLIC_DIR / 'favicon.png'} {favicon.size}")

    touch = render_touch_icon(180)
    touch.save(PUBLIC_DIR / "apple-touch-icon.png")
    print(f"wrote {PUBLIC_DIR / 'apple-touch-icon.png'} {touch.size}")


if __name__ == "__main__":
    main()
