#!/usr/bin/env python3
"""Stage 1 - read a .pptx: pull speaker notes + text, render slides to stills.

  python3 pipeline/extract.py deck.pptx --out work/

Writes work/deck.json, work/slides/slide-NN.png and work/narration.draft.json
(a skeleton for the narration writer to fill in).
"""
from __future__ import annotations
import argparse, copy, os, re, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import W, H, die, log, run, save_json

from pptx import Presentation
from pptx.util import Emu
from PIL import Image


def _text_of(shape) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    return "\n".join(p.text for p in shape.text_frame.paragraphs).strip()


def _slide_title(slide) -> str:
    try:
        if slide.shapes.title is not None:
            t = _text_of(slide.shapes.title)
            if t:
                return re.sub(r"\s+", " ", t)
    except Exception:
        pass
    # fall back to the topmost non-empty text shape
    best, best_top = "", None
    for sh in slide.shapes:
        t = _text_of(sh)
        if not t:
            continue
        top = sh.top if sh.top is not None else Emu(0)
        if best_top is None or top < best_top:
            best, best_top = re.sub(r"\s+", " ", t.splitlines()[0]), top
    return best[:160]


def _body_lines(slide, title: str) -> list[str]:
    out: list[str] = []
    for sh in slide.shapes:
        t = _text_of(sh)
        if not t:
            continue
        for line in t.splitlines():
            line = re.sub(r"\s+", " ", line).strip(" \t•-–—")
            if line and line != title and line not in out:
                out.append(line)
    return out[:40]


def _notes(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    tf = slide.notes_slide.notes_text_frame
    if tf is None:
        return ""
    # Drop the slide-number placeholder text LibreOffice/PowerPoint sometimes adds.
    lines = [re.sub(r"\s+", " ", p.text).strip() for p in tf.paragraphs]
    lines = [l for l in lines if l and not re.fullmatch(r"\d{1,3}", l)]
    return "\n".join(lines).strip()


def _is_hidden(slide) -> bool:
    return slide._element.get("show") == "0"


def _unhidden_copy(src: Path, tmpdir: Path) -> Path:
    """LibreOffice omits hidden slides from PDF export, which would shift page
    numbers. Export from a copy with every slide shown so page N == slide N."""
    prs = Presentation(str(src))
    changed = False
    for slide in prs.slides:
        if _is_hidden(slide):
            slide._element.set("show", "1")
            changed = True
    if not changed:
        return src
    out = tmpdir / "all-slides-shown.pptx"
    prs.save(str(out))
    return out


def render_slides(pptx: Path, outdir: Path, dpi: int) -> list[Path]:
    tmp = outdir / ".render"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    src = _unhidden_copy(pptx, tmp).resolve()
    profile = (tmp / "lo").resolve()
    log("converting to PDF with LibreOffice ...")
    # UserInstallation must be an absolute file:// URI or soffice hangs.
    r = subprocess.run(
        ["libreoffice", "--headless", "--norestore", "--invisible", "--nolockcheck",
         "--nodefault", "--nofirststartwizard",
         f"-env:UserInstallation=file://{profile}",
         "--convert-to", "pdf", "--outdir", str(tmp.resolve()), str(src)],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        die("LibreOffice conversion failed:\n" + (r.stderr or r.stdout)[-800:])
    pdfs = sorted(tmp.glob("*.pdf"))
    if not pdfs:
        die("LibreOffice produced no PDF - is the .pptx readable?")
    log(f"rasterising pages at {dpi} dpi ...")
    run(["pdftoppm", "-r", str(dpi), "-png", str(pdfs[0]), str(tmp / "page")])
    pages = sorted(tmp.glob("page-*.png")) or sorted(tmp.glob("page*.png"))
    if not pages:
        die("pdftoppm produced no pages")

    slides_dir = outdir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    finals: list[Path] = []
    for i, page in enumerate(pages, start=1):
        finals.append(_fit_canvas(page, slides_dir / f"slide-{i:02d}.png"))
    shutil.rmtree(tmp, ignore_errors=True)
    return finals


def _fit_canvas(src: Path, dst: Path) -> Path:
    """Fit the page into a 1920x1080 frame, padded with the slide's own
    background colour so 4:3 decks don't sit in a black box."""
    im = Image.open(src).convert("RGB")
    bg = _edge_colour(im)
    scale = min(W / im.width, H / im.height)
    new = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(new, Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), bg)
    canvas.paste(im, ((W - new[0]) // 2, (H - new[1]) // 2))
    canvas.save(dst, "PNG")
    return dst


def _edge_colour(im: Image.Image) -> tuple[int, int, int]:
    s = im.resize((40, 40), Image.BILINEAR)
    px = [s.getpixel((x, y)) for x in range(40) for y in range(40)
          if x in (0, 1, 38, 39) or y in (0, 1, 38, 39)]
    n = len(px) or 1
    return tuple(sum(c[i] for c in px) // n for i in range(3))  # type: ignore


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract notes and slide stills from a .pptx")
    ap.add_argument("pptx")
    ap.add_argument("--out", default="work", help="working directory (default: work)")
    ap.add_argument("--dpi", type=int, default=150, help="rasterisation dpi (default: 150)")
    ap.add_argument("--no-render", action="store_true", help="notes only, skip slide images")
    a = ap.parse_args()

    pptx = Path(a.pptx).expanduser()
    if not pptx.exists():
        die(f"no such file: {pptx}")
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    prs = Presentation(str(pptx))
    slides = list(prs.slides)
    log(f"{len(slides)} slides in {pptx.name}")

    images: list[Path] = []
    if not a.no_render:
        images = render_slides(pptx, out, a.dpi)
        if len(images) != len(slides):
            log(f"WARNING: rendered {len(images)} pages for {len(slides)} slides; "
                "pairing by position")

    records = []
    for i, slide in enumerate(slides, start=1):
        title = _slide_title(slide)
        img = images[i - 1] if i - 1 < len(images) else None
        records.append({
            "n": i,
            "title": title,
            "body": _body_lines(slide, title),
            "notes": _notes(slide),
            "hidden": _is_hidden(slide),
            "image": str(img.relative_to(out)) if img else None,
        })

    with_notes = sum(1 for r in records if r["notes"])
    deck_title = next((r["title"] for r in records if r["title"]), pptx.stem)
    deck = {
        "source": str(pptx),
        "deck_title": deck_title,
        "aspect": round(prs.slide_width / prs.slide_height, 4) if prs.slide_height else 1.7778,
        "slide_count": len(records),
        "slides_with_notes": with_notes,
        "slides": records,
    }
    save_json(out / "deck.json", deck)

    draft = {
        "deck_title": deck_title,
        "voice": None,
        "intro": None,
        "outro": None,
        "slides": [
            {"n": r["n"], "skip": bool(r["hidden"]) or not r["notes"],
             "narration": "", "_title": r["title"], "_notes": r["notes"]}
            for r in records
        ],
    }
    save_json(out / "narration.draft.json", draft)

    log(f"wrote {out/'deck.json'} ({with_notes}/{len(records)} slides have speaker notes)")
    if with_notes == 0:
        log("WARNING: no speaker notes found - narration would have nothing to expand")
    print(str(out / "deck.json"))


if __name__ == "__main__":
    main()
