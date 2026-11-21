#!/usr/bin/env python3
"""Derive the site's background art from the SlideToVideo illustration.

  python3 tools/make_assets.py path/to/illustration.jpg

Writes webapp/static/assets/hero.webp  - trimmed, edges faded to transparent
       webapp/static/assets/ambient.jpg - chroma-only blur for the page wash
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "webapp" / "static" / "assets"


def trim(im: Image.Image, tol: int = 10) -> Image.Image:
    """Crop the flat outer margin, keeping a little breathing room."""
    a = np.asarray(im.convert("RGB")).astype(int)
    bg = np.median(np.concatenate([a[:40].reshape(-1, 3), a[-40:].reshape(-1, 3)]), axis=0)
    diff = np.abs(a - bg).sum(axis=2)
    rows = np.where(diff.max(axis=1) > tol)[0]
    cols = np.where(diff.max(axis=0) > tol)[0]
    if not len(rows) or not len(cols):
        return im
    pad = 24
    return im.crop((max(0, cols[0] - pad), max(0, rows[0] - pad),
                    min(im.width, cols[-1] + pad), min(im.height, rows[-1] + pad)))


def hero(im: Image.Image, width: int = 1240) -> Image.Image:
    """Settle the art into a dark page: roll the near-whites down so it stops
    reading as a spotlight, cool them toward the page's blue, and fade the
    rim to transparent so there is no hard rectangle."""
    im = trim(im)
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    a = np.asarray(im.convert("RGB")).astype(float) / 255.0

    lum = a @ np.array([0.299, 0.587, 0.114])
    # smoothstep over the top of the range: only the brightest areas move,
    # so dark text on a white card keeps its contrast.
    t = np.clip((lum - 0.62) / 0.38, 0, 1)
    t = (t * t * (3 - 2 * t))[..., None]
    a = a * (1 - 0.34 * t)
    a = a * (1 - 0.16 * t) + np.array([0.10, 0.13, 0.19]) * (0.16 * t)

    w, h = im.size
    y, x = np.mgrid[0:h, 0:w]
    r = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
    alpha = np.clip((1.16 - r) / 0.58, 0, 1) ** 1.15

    out = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).convert("RGBA")
    out.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
    return out


def ambient(im: Image.Image, width: int = 560, blur: int = 26,
            boost: float = 2.6) -> Image.Image:
    """Keep only the colour, drop the light base. Greys become black, which
    contributes nothing under `mix-blend-mode: screen`, so what reaches the
    page is the artwork's colour and none of its white background."""
    im = trim(im)
    small = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    small = small.filter(ImageFilter.GaussianBlur(blur))
    a = np.asarray(small.convert("RGB")).astype(float) / 255.0
    chroma = np.clip((a - a.min(axis=2, keepdims=True)) * boost, 0, 1)
    chroma = chroma ** 0.85
    return Image.fromarray((chroma * 255).astype(np.uint8))


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "")
    if not src.exists():
        sys.exit(f"usage: make_assets.py <illustration.jpg>  (no such file: {src})")
    im = Image.open(src).convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    h = hero(im);     h.save(OUT / "hero.webp", quality=92, method=6)
    a = ambient(im);  a.save(OUT / "ambient.jpg", quality=82, optimize=True)
    for p in ("hero.webp", "ambient.jpg"):
        f = OUT / p
        print(f"{p:14} {Image.open(f).size}  {f.stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()
