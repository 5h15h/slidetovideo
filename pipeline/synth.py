#!/usr/bin/env python3
"""Stage 3 - turn narration.json into voiced audio plus an exact timeline.

  python3 pipeline/synth.py work/ --tts auto --wpm 150

Writes work/audio/program.wav (mastered), work/timeline.json and
work/captions.srt. The timeline is authoritative: the video is cut to it.
"""
from __future__ import annotations
import argparse, json, re, sys, wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tts as tts_mod
from common import (die, ff, load_json, log, run, save_json, split_sentences,
                    srt_time, wrap_caption)

SR = 48000

# Pacing. These gaps are what make narration sound unhurried rather than
# machine-gunned; they are deliberately generous.
DEFAULTS = dict(head=0.45, sentence=0.34, para=0.62, tail=0.85,
                card_head=0.70, card_tail=1.10, min_slide=4.0)


def read_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        tmp = path.with_suffix(".norm.wav")
        ff(["-i", str(path), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(tmp)])
        try:
            return read_wav_mono(tmp)
        finally:
            tmp.unlink(missing_ok=True)
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        n_out = int(round(len(a) * SR / sr))
        a = np.interp(np.linspace(0, len(a) - 1, n_out),
                      np.arange(len(a)), a).astype(np.float32)
    return a


def write_wav_mono(path: Path, a: np.ndarray) -> None:
    a = np.clip(a, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((a * 32767.0).astype("<i2").tobytes())


def trim_silence(a: np.ndarray, thresh_db: float = -45.0, keep_ms: int = 45) -> np.ndarray:
    """Engines pad their own leading/trailing silence; strip it so the pause
    lengths below are the only thing deciding rhythm."""
    if a.size == 0:
        return a
    win = max(1, int(SR * 0.01))
    pad = (-len(a)) % win
    frames = np.abs(np.concatenate([a, np.zeros(pad, np.float32)])).reshape(-1, win).max(axis=1)
    thr = 10 ** (thresh_db / 20.0)
    loud = np.where(frames > thr)[0]
    if loud.size == 0:
        return a
    keep = int(SR * keep_ms / 1000)
    s = max(0, loud[0] * win - keep)
    e = min(len(a), (loud[-1] + 1) * win + keep)
    return a[s:e]


def silence(sec: float) -> np.ndarray:
    return np.zeros(max(0, int(round(sec * SR))), dtype=np.float32)


def paragraphs_of(text: str) -> list[list[str]]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return [split_sentences(p) for p in paras if split_sentences(p)]


def master(src: Path, dst: Path) -> None:
    """Two-pass loudness normalisation to broadcast-ish speech levels.
    Every filter here is duration-preserving, so the timeline stays valid."""
    pre = ("highpass=f=80,"
           "acompressor=threshold=-18dB:ratio=2.5:attack=8:release=180:makeup=1")
    log("measuring loudness ...")
    r = run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(src), "-af",
             pre + ",loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"], check=False)
    blob = r.stderr[r.stderr.rfind("{"): r.stderr.rfind("}") + 1]
    ln = None
    try:
        m = json.loads(blob)
        vals = {k: float(m[f"input_{k}"]) for k in ("i", "tp", "lra", "thresh")}
        off = float(m["target_offset"])
        # Near-silent input measures as -inf and would crash the second pass.
        if all(v == v and abs(v) != float("inf") for v in (*vals.values(), off)) \
           and -99 <= vals["i"] < 0:
            ln = (f"loudnorm=I=-16:TP=-1.5:LRA=11:"
                  f"measured_I={vals['i']}:measured_TP={vals['tp']}:"
                  f"measured_LRA={vals['lra']}:measured_thresh={vals['thresh']}:"
                  f"offset={off}:linear=true")
    except Exception:
        pass
    if ln is None:
        log("no usable loudness measurement (silent or very quiet audio); "
            "copying levels through")
        ff(["-i", str(src), "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
        return
    log("mastering ...")
    ff(["-i", str(src), "-af", f"{pre},{ln},alimiter=limit=0.95",
        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(dst)])


progress_cb = None


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthesize narration and build the timeline")
    ap.add_argument("work")
    ap.add_argument("--narration", default=None, help="default: <work>/narration.json")
    ap.add_argument("--tts", default="auto",
                    choices=["auto", "eleven", "mai", "azure", "edge", "piper",
                             "say", "flite", "silent"])
    ap.add_argument("--tts-settings", default=None,
                    help="JSON file of backend settings (ElevenLabs key, voice, sliders)")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--style", default=None, help="Azure speaking style, e.g. narration-professional")
    ap.add_argument("--wpm", type=int, default=150, help="target speaking pace")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_','-')}", type=float, default=v)
    ap.add_argument("--keep-cues", action="store_true", help="keep per-sentence wavs")
    a = ap.parse_args()

    work = Path(a.work)
    deck = load_json(work / "deck.json")
    nar_path = Path(a.narration) if a.narration else work / "narration.json"
    nar = load_json(nar_path)

    voice = a.voice or nar.get("voice")
    cfg = load_json(a.tts_settings) if a.tts_settings else None
    backend = tts_mod.build(a.tts, voice, a.wpm, a.style, cfg)
    log(f"voice backend: {backend.describe()}")

    by_n = {s["n"]: s for s in deck["slides"]}
    cue_dir = work / "audio" / "cues"
    cue_dir.mkdir(parents=True, exist_ok=True)

    # Build the ordered list of segments: optional intro card, slides, optional outro.
    segments = []
    if (nar.get("intro") or "").strip():
        segments.append(dict(kind="intro", n=0, title=nar.get("deck_title") or deck["deck_title"],
                             subtitle=nar.get("intro_subtitle") or "", image=None,
                             text=nar["intro"]))
    for s in nar.get("slides", []):
        n = int(s["n"])
        if s.get("skip") or not (s.get("narration") or "").strip():
            continue
        src = by_n.get(n)
        if src is None:
            die(f"narration references slide {n}, which is not in the deck")
        if not src.get("image"):
            die(f"slide {n} has no rendered image; re-run extract.py without --no-render")
        segments.append(dict(kind="slide", n=n, title=src["title"], subtitle="",
                             image=src["image"], text=s["narration"]))
    if (nar.get("outro") or "").strip():
        segments.append(dict(kind="outro", n=999,
                             title=nar.get("outro_title") or "Thanks for watching",
                             subtitle="", image=None, text=nar["outro"]))

    if not segments:
        die("nothing to narrate: every slide was skipped or had empty narration")

    total_cues = sum(len(c) for seg in segments for c in paragraphs_of(seg["text"]))
    log(f"{len(segments)} segments, {total_cues} cues to synthesize")

    program: list[np.ndarray] = []
    cursor = 0.0
    timeline = []
    done = 0

    for si, seg in enumerate(segments):
        is_card = seg["kind"] in ("intro", "outro")
        head = a.card_head if is_card else a.head
        tail = a.card_tail if is_card else a.tail

        seg_start = cursor
        chunks: list[np.ndarray] = [silence(head)]
        cursor += head
        cues = []

        paras = paragraphs_of(seg["text"])
        flat: list[tuple[str, float]] = []
        for pi, sentences in enumerate(paras):
            for ci, sentence in enumerate(sentences):
                last_in_para = ci == len(sentences) - 1
                last_overall = last_in_para and pi == len(paras) - 1
                gap = 0.0 if last_overall else (a.para if last_in_para else a.sentence)
                flat.append((sentence, gap))

        for k, (sentence, gap) in enumerate(flat):
            done += 1
            wav = cue_dir / f"seg{si:02d}-{k:03d}.wav"
            # Neighbouring cues give engines that support it the context to
            # keep prosody continuous across a sentence-by-sentence render.
            backend.synth(sentence, wav,
                          prev=flat[k - 1][0] if k > 0 else None,
                          nxt=flat[k + 1][0] if k + 1 < len(flat) else None)
            audio = trim_silence(read_wav_mono(wav))
            if not a.keep_cues:
                wav.unlink(missing_ok=True)
            dur = len(audio) / SR
            cues.append(dict(start=round(cursor, 3), end=round(cursor + dur, 3),
                             text=tts_mod.plain_text(sentence)))
            chunks.append(audio)
            cursor += dur
            if gap:
                chunks.append(silence(gap)); cursor += gap
            if done % 5 == 0 or done == total_cues:
                log(f"  synthesized {done}/{total_cues}")
            if progress_cb:
                progress_cb(done, total_cues)

        chunks.append(silence(tail)); cursor += tail

        # A slide that flashes past is unwatchable; hold the floor with silence.
        seg_dur = cursor - seg_start
        if not is_card and seg_dur < a.min_slide:
            extra = a.min_slide - seg_dur
            chunks.append(silence(extra)); cursor += extra; seg_dur = a.min_slide

        program.append(np.concatenate(chunks))
        timeline.append(dict(kind=seg["kind"], n=seg["n"], title=seg["title"],
                             subtitle=seg["subtitle"], image=seg["image"],
                             start=round(seg_start, 3), end=round(cursor, 3),
                             duration=round(seg_dur, 3), cues=cues))

    audio_dir = work / "audio"
    raw = audio_dir / "program.raw.wav"
    write_wav_mono(raw, np.concatenate(program))
    out = audio_dir / "program.wav"
    master(raw, out)
    raw.unlink(missing_ok=True)
    try:
        cue_dir.rmdir()
    except OSError:
        pass

    total = round(cursor, 3)
    save_json(work / "timeline.json", dict(
        deck_title=nar.get("deck_title") or deck["deck_title"],
        audio="audio/program.wav", total=total,
        backend=backend.describe(), wpm=a.wpm, segments=timeline))

    # Captions
    lines, idx = [], 1
    for seg in timeline:
        for c in seg["cues"]:
            lines.append(f"{idx}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n"
                         f"{wrap_caption(c['text'])}\n")
            idx += 1
    (work / "captions.srt").write_text("\n".join(lines))

    m, s = divmod(total, 60)
    log(f"program: {int(m)}m {s:04.1f}s  ->  {out}")
    log(f"timeline: {work/'timeline.json'}   captions: {work/'captions.srt'}")
    print(str(work / "timeline.json"))


if __name__ == "__main__":
    main()
