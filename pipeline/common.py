"""Shared helpers for the ppt2video pipeline."""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys
from pathlib import Path

W, H, FPS = 1920, 1080, 30

def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)

def run(cmd: list[str], quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-18:]
        die(f"command failed ({cmd[0]} exit {r.returncode}):\n" + "\n".join(tail))
    return r

def ff(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with consistent global flags."""
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=check)

def duration(path: str | Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        die(f"could not read duration of {path}")
        return 0.0

def atomic_rename(tmp: Path, final: Path) -> None:
    """Only publish a render once the encoder exited cleanly."""
    tmp.replace(final)

def load_json(path: str | Path):
    p = Path(path)
    if not p.exists():
        die(f"missing file: {p}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        die(f"{p} is not valid JSON: {e}")

def save_json(path: str | Path, obj) -> None:
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False))

def srt_time(t: float) -> str:
    if t < 0: t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# --- sentence splitting -------------------------------------------------------
_ABBREV = (r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bvs\.)"
           r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bFig\.)(?<!\bNo\.)(?<!\bU\.S\.)"
           r"(?<!\bInc\.)(?<!\bCo\.)(?<!\bapprox\.)(?<![A-Z]\.)")

def split_sentences(text: str) -> list[str]:
    """Split narration into caption/synthesis cues. Keeps terminal punctuation."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(rf"{_ABBREV}(?<=[.!?])(?=\s+[\"'“(\[]?[A-Z0-9])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # A very long sentence is hard to caption; break it at a clause boundary.
        if len(p) > 220:
            chunks = re.split(r"(?<=[,;:—])\s+", p)
            buf = ""
            for c in chunks:
                if buf and len(buf) + len(c) + 1 > 180:
                    out.append(buf.strip()); buf = c
                else:
                    buf = f"{buf} {c}".strip()
            if buf:
                out.append(buf.strip())
        else:
            out.append(p)
    return out

def wrap_caption(text: str, max_chars: int = 46, max_lines: int = 2) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + len(w) + 1 > max_chars:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:  # rebalance into max_lines
        per = max(1, len(text) // max_lines + 1)
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + len(w) + 1 > per and len(lines) < max_lines - 1:
                lines.append(cur); cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
    return "\n".join(lines)


# --- narration markup -> SSML -------------------------------------------------
def to_ssml_fragment(text: str) -> str:
    """*word* -> emphasis, ... -> a beat, -- / em dash -> a shorter beat."""
    import html
    t = html.escape(text, quote=False)
    t = t.replace("\u2014", ' <break time="180ms"/> ')
    t = t.replace("--", ' <break time="180ms"/> ')
    t = t.replace("\u2026", ' <break time="260ms"/> ')
    t = t.replace("...", ' <break time="260ms"/> ')
    out, i = [], 0
    while True:
        a = t.find("*", i)
        if a == -1:
            out.append(t[i:]); break
        b = t.find("*", a + 1)
        if b == -1:
            out.append(t[i:]); break
        out.append(t[i:a])
        out.append(f'<emphasis level="moderate">{t[a+1:b]}</emphasis>')
        i = b + 1
    return "".join(out).strip()


def plain_text(text: str) -> str:
    """Markup stripped, for backends with no SSML and for captions."""
    return (text.replace("*", "")
                .replace("\u2026", "...")
                .replace("  ", " ").strip())
