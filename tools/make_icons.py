#!/usr/bin/env python3
"""Generate the PWA icon set from the brand mark.

  python3 tools/make_icons.py

A rounded square carrying the accent→violet gradient and a play glyph, which
is the same mark the header uses. The maskable variant keeps the glyph inside
the 80% safe area so Android/Chrome can crop it to any shape.
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "webapp" / "static" / "assets"
ACCENT = (0x5B, 0x8C, 0xFF)
VIOLET = (0x8B, 0x5C, 0xF6)
SS = 4  # supersample factor


def gradient(size: int) -> Image.Image:
    g = Image.new("RGB", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(ACCENT[i] + (VIOLET[i] - ACCENT[i]) * t)
                             for i in range(3))
    return g


def play(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    # Slight optical offset: a triangle looks left-heavy when centred on bounds.
    x0 += w * 0.08
    draw.polygon([(x0, y0), (x0, y1), (x0 + w * 0.92, y0 + h / 2)], fill=255)


def icon(size: int, maskable: bool = False, radius_ratio: float = 0.22) -> Image.Image:
    s = size * SS
    base = gradient(s)

    glyph = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(glyph)
    inset = 0.30 if maskable else 0.26      # smaller glyph inside the safe area
    play(d, (s * inset, s * inset, s * (1 - inset), s * (1 - inset)))
    white = Image.new("RGB", (s, s), (255, 255, 255))
    base = Image.composite(white, base, glyph)

    if maskable:
        # Full bleed: the launcher crops it, so no rounded corners of our own.
        out = base
        alpha = Image.new("L", (s, s), 255)
    else:
        alpha = Image.new("L", (s, s), 0)
        ImageDraw.Draw(alpha).rounded_rectangle(
            [0, 0, s - 1, s - 1], radius=int(s * radius_ratio), fill=255)
        out = base

    img = out.convert("RGBA")
    img.putalpha(alpha)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("icon-192.png", icon(192)),
        ("icon-512.png", icon(512)),
        ("icon-maskable-512.png", icon(512, maskable=True)),
        ("apple-touch-icon.png", icon(180, radius_ratio=0.0)),
        ("favicon-32.png", icon(32, radius_ratio=0.26)),
    ]
    for name, im in jobs:
        im.save(OUT / name, "PNG", optimize=True)
        print(f"{name:26} {im.size[0]}x{im.size[1]}  {(OUT/name).stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()
