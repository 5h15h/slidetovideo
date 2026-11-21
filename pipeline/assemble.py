#!/usr/bin/env python3
"""Stage 4 - cut the video to the timeline and mux the narration.

  python3 pipeline/assemble.py work/ --out onboarding.mp4

One encoded file per scene, identical encoder settings, joined with the concat
demuxer and -c copy; audio muxed with -c:v copy. Crossfades live inside each
scene file so nothing is encoded twice.
"""
from __future__ import annotations
import argparse, colorsys, json, shutil, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import W, H, FPS, die, ff, load_json, log, run, duration

FONT_DIR = Path("/usr/share/fonts/opentype/inter")
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
XFADE = 0.5          # seconds, lives at the head of each scene
VENC = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2),
        "-video_track_timescale", "15360"]


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for cand in [FONT_DIR / f"Inter-{weight}.otf", *FALLBACK_FONTS]:
        if Path(cand).exists():
            try:
                return ImageFont.truetype(str(cand), size)
            except Exception:
                continue
    return ImageFont.load_default()


# --- theme -------------------------------------------------------------------
def edge_colour(im: Image.Image) -> tuple[int, int, int]:
    s = im.resize((48, 48), Image.BILINEAR).convert("RGB")
    px = [s.getpixel((x, y)) for x in range(48) for y in range(48)
          if x < 2 or x > 45 or y < 2 or y > 45]
    n = len(px) or 1
    return tuple(sum(c[i] for c in px) // n for i in range(3))  # type: ignore


def detect_accent(images: list[Path], default="#4C9AFF") -> str:
    """Borrow the deck's own accent colour so the cards and progress bar match."""
    hues, sats = [], []
    for p in images[:8]:
        a = np.asarray(Image.open(p).convert("RGB").resize((160, 90))) / 255.0
        mx, mn = a.max(axis=2), a.min(axis=2)
        v, c = mx, mx - mn
        s = np.where(mx > 0, c / np.maximum(mx, 1e-6), 0)
        m = (s > 0.45) & (v > 0.45)
        if m.sum() < 40:
            continue
        for r, g, b in a[m][:4000]:
            hh, _, _ = colorsys.rgb_to_hsv(r, g, b)
            hues.append(hh); sats.append(float(s[m].mean()))
    if len(hues) < 40:
        return default
    h = float(np.median(hues))
    r, g, b = colorsys.hsv_to_rgb(h, min(0.82, max(0.55, float(np.median(sats)))), 1.0)
    return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))


def hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


# --- cards -------------------------------------------------------------------
def wrap_to_width(draw, text, fnt, max_w) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=fnt) > max_w:
            out.append(cur); cur = word
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def make_card(path: Path, title: str, subtitle: str, bg: tuple, accent: str) -> Path:
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    lum = (0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]) / 255
    ink = (18, 22, 28) if lum > 0.6 else (244, 247, 251)
    muted = tuple(int(ink[i] * 0.62 + bg[i] * 0.38) for i in range(3))

    size = 92 if len(title) < 34 else 70
    ft, fs = font("Bold", size), font("Regular", 34)
    lines = wrap_to_width(d, title, ft, W - 440)
    lh = int(size * 1.18)
    block = lh * len(lines) + (58 + 34 if subtitle else 0)
    y = (H - block) // 2

    d.rounded_rectangle([220, y - 46, 220 + 108, y - 46 + 7], 4, fill=hex_rgb(accent))
    for ln in lines:
        d.text((220, y), ln, font=ft, fill=ink); y += lh
    if subtitle:
        d.text((220, y + 22), subtitle, font=fs, fill=muted)
    im.save(path, "PNG")
    return path


# --- scenes ------------------------------------------------------------------
def motion_filter(dur: float, idx: int, zoom: float, upscale: int) -> str:
    """Slow Ken Burns on a pre-upscaled still: 4x source keeps zoompan's
    integer stepping below a visible threshold."""
    frames = max(2, int(round(dur * FPS)))
    if zoom <= 0:
        return f"scale={W}:{H}:flags=lanczos"
    if idx % 2 == 0:
        z = f"1+{zoom:.4f}*on/{frames - 1}"          # push in
    else:
        z = f"{1 + zoom:.4f}-{zoom:.4f}*on/{frames - 1}"  # pull out
    return (f"scale={W * upscale}:{H * upscale}:flags=lanczos,"
            f"zoompan=z='{z}':d={frames}:s={W}x{H}:fps={FPS}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")


def ff_colour(hexs: str) -> str:
    return "0x" + hexs.lstrip("#")


def base_chain(dur: float, idx: int, zoom: float, upscale: int, track: bool) -> str:
    """Motion + exact trim + the static progress track."""
    chain = (f"{motion_filter(dur, idx, zoom, upscale)},"
             f"trim=duration={dur:.3f},setpts=PTS-STARTPTS")
    if track:
        chain += ",drawbox=x=0:y=ih-6:w=iw:h=6:color=white@0.10:t=fill"
    return chain + ",format=yuv420p,setsar=1"


def progress_overlay(start: float, total: float, dur: float, accent: str) -> tuple[str, str]:
    """A full-width accent bar slid in from the left. overlay is used rather
    than drawbox because inside drawbox `t` means thickness, not time."""
    src = (f"color=c={ff_colour(accent)}:s={W}x6:d={dur:.3f}:r={FPS},"
           f"format=rgba[bar]")
    x = rf"-{W}*(1-min(1\,({start:.3f}+t)/{total:.3f}))"
    ov = f"overlay=x='{x}':y=H-6:eval=frame:format=auto"
    return src, ov


def render_scene(still: Path, dur: float, idx: int, start: float, total: float,
                 accent: str, zoom: float, upscale: int, prev_tail: Path | None,
                 out: Path, bar: bool = True) -> None:
    base = base_chain(dur, idx, zoom, upscale, bar)
    parts, last = [], None
    if prev_tail is None:
        inputs = ["-framerate", str(FPS), "-loop", "1", "-t", f"{dur:.3f}", "-i", str(still)]
        parts.append(f"[0:v]{base}[main]")
    else:
        inputs = ["-framerate", str(FPS), "-loop", "1", "-t", f"{XFADE:.3f}",
                  "-i", str(prev_tail),
                  "-framerate", str(FPS), "-loop", "1", "-t", f"{dur:.3f}", "-i", str(still)]
        # xfade output = XFADE + dur - XFADE = dur, so the timeline holds.
        parts.append(f"[0:v]scale={W}:{H},format=yuv420p,setsar=1,fps={FPS}[a]")
        parts.append(f"[1:v]{base}[b]")
        parts.append(f"[a][b]xfade=transition=fade:duration={XFADE}:offset=0,"
                     f"format=yuv420p[main]")
    if bar and total > 0:
        src, ov = progress_overlay(start, total, dur, accent)
        parts.append(src)
        parts.append(f"[main][bar]{ov},format=yuv420p[v]")
        last = "[v]"
    else:
        last = "[main]"
    tmp = out.with_name(out.stem + ".part.mp4")
    ff([*inputs, "-filter_complex", ";".join(parts), "-map", last, *VENC, str(tmp)])
    tmp.replace(out)


def last_frame(video: Path, out: Path) -> Path:
    ff(["-sseof", "-0.2", "-i", str(video), "-frames:v", "1", "-q:v", "2",
        "-update", "1", str(out)])
    return out


def write_chapters(path: Path, segments: list[dict], total: float) -> Path:
    lines = [";FFMETADATA1"]
    for s in segments:
        title = (s.get("title") or f"Slide {s['n']}").replace("\n", " ")
        for ch in ("\\", "=", ";", "#"):
            title = title.replace(ch, "\\" + ch)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={int(s['start'] * 1000)}",
                  f"END={int(min(s['end'], total) * 1000)}",
                  f"title={title}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble the narrated video")
    ap.add_argument("work")
    ap.add_argument("--out", default=None, help="output .mp4")
    ap.add_argument("--captions", choices=["srt", "burn", "none"], default="srt")
    ap.add_argument("--zoom", type=float, default=0.035,
                    help="Ken Burns travel, 0 disables motion")
    ap.add_argument("--upscale", type=int, default=2, help="zoompan supersampling")
    ap.add_argument("--accent", default=None, help="#RRGGBB, default: taken from the deck")
    ap.add_argument("--music", default=None, help="background bed, ducked under speech")
    ap.add_argument("--music-db", type=float, default=-26.0)
    ap.add_argument("--preview", action="store_true",
                    help="write full-size sample frames and stop")
    ap.add_argument("--keep-scenes", action="store_true")
    ap.add_argument("--reuse-scenes", action="store_true",
                    help="skip scenes already rendered in work/scenes")
    a = ap.parse_args()

    work = Path(a.work)
    tl = load_json(work / "timeline.json")
    segs = tl["segments"]
    total = float(tl["total"])
    audio = work / tl["audio"]
    if not audio.exists():
        die(f"missing narration audio: {audio}")

    out = Path(a.out) if a.out else work / "video.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    scenes_dir = work / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    slide_imgs = [work / s["image"] for s in segs if s.get("image")]
    if not slide_imgs:
        die("no slide images in the timeline")
    accent = a.accent or detect_accent(slide_imgs)
    bg = edge_colour(Image.open(slide_imgs[0]))
    log(f"accent {accent}  card background rgb{bg}")

    # Resolve a still for every segment (cards are generated).
    stills: list[Path] = []
    for i, s in enumerate(segs):
        if s.get("image"):
            stills.append(work / s["image"])
        else:
            card = scenes_dir / f"card-{i:02d}.png"
            make_card(card, s.get("title") or tl["deck_title"],
                      s.get("subtitle") or "", bg, accent)
            stills.append(card)

    if a.preview:
        pv = work / "preview"
        pv.mkdir(exist_ok=True)
        picks = sorted({0, len(segs) // 2, len(segs) - 1})
        for i in picks:
            s = segs[i]
            tmp = pv / f"scene-{i:02d}.mp4"
            render_scene(stills[i], min(2.0, s["duration"]), i, s["start"], total,
                         accent, a.zoom, a.upscale, None, tmp)
            ff(["-sseof", "-0.1", "-i", str(tmp), "-frames:v", "1", "-update", "1",
                str(pv / f"frame-{i:02d}.png")])
            tmp.unlink(missing_ok=True)
        log(f"preview frames in {pv}")
        print(str(pv)); return

    log(f"rendering {len(segs)} scenes ...")
    scene_files: list[Path] = []
    for i, s in enumerate(segs):
        f = scenes_dir / f"scene-{i:02d}.mp4"
        if a.reuse_scenes and f.exists() and f.stat().st_size > 2048:
            log(f"  scene {i+1}/{len(segs)}  reused")
            scene_files.append(f); continue
        # The crossfade needs the previous scene's final frame; take it lazily
        # so a run of reused scenes costs nothing.
        tail = (last_frame(scene_files[i - 1], scenes_dir / f"tail-{i-1:02d}.png")
                if i > 0 else None)
        render_scene(stills[i], s["duration"], i, s["start"], total,
                     accent, a.zoom, a.upscale, tail, f)
        scene_files.append(f)
        log(f"  scene {i+1}/{len(segs)}  {s['duration']:.2f}s  {(s.get('title') or '')[:36]}")

    lst = scenes_dir / "concat.txt"
    lst.write_text("".join(f"file '{f.resolve()}'\n" for f in scene_files))
    silent = scenes_dir / "silent.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])

    vdur = duration(silent)
    if abs(vdur - total) > 0.3:
        log(f"WARNING: video {vdur:.2f}s vs timeline {total:.2f}s")

    meta = write_chapters(scenes_dir / "chapters.txt", segs, total)

    # Audio: narration, optionally over a ducked music bed.
    mux = ["-i", str(silent), "-i", str(audio)]
    if a.music:
        if not Path(a.music).exists():
            die(f"no such music file: {a.music}")
        mux += ["-stream_loop", "-1", "-i", a.music]
        filt = (f"[2:a]volume={a.music_db}dB,afade=t=in:st=0:d=2.5[bed];"
                f"[bed][1:a]sidechaincompress=threshold=0.05:ratio=8:attack=12:"
                f"release=420[duck];[duck][1:a]amix=inputs=2:duration=shortest:"
                f"normalize=0,alimiter=limit=0.95[a]")
        amap = ["-filter_complex", filt, "-map", "0:v", "-map", "[a]"]
    else:
        amap = ["-map", "0:v", "-map", "1:a"]

    log("muxing ...")
    tmp_out = out.with_name(out.stem + ".part.mp4")
    ff([*mux, "-i", str(meta), "-map_metadata", "2", *amap,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(tmp_out)])
    tmp_out.replace(out)

    if a.captions == "burn":
        log("burning captions (one re-encode) ...")
        srt = work / "captions.srt"
        style = ("FontName=Inter,FontSize=21,PrimaryColour=&H00FFFFFF,"
                 "BackColour=&H40000000,BorderStyle=4,Outline=0,Shadow=0,"
                 "MarginV=42,Alignment=2")
        burned = out.with_name(out.stem + ".captioned.part.mp4")
        ff(["-i", str(out), "-vf",
            f"subtitles={srt.resolve()}:force_style='{style}'",
            *VENC, "-c:a", "copy", "-movflags", "+faststart", str(burned)])
        burned.replace(out)

    if not a.keep_scenes:
        shutil.rmtree(scenes_dir, ignore_errors=True)

    log(f"done: {out}  ({duration(out):.1f}s)")
    print(str(out))


if __name__ == "__main__":
    main()
