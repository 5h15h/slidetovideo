#!/usr/bin/env python3
"""SlideToVideo - local web app that turns a deck into a narrated video.

    python3 webapp/server.py            # http://127.0.0.1:8808

Binds to localhost only. API keys are held in memory for the life of a
request or render job and are never written to disk.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, request,
                   send_file, send_from_directory)
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
JOBS_DIR = Path(os.environ.get("PPT2VIDEO_JOBS", ROOT / "jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PIPELINE))

import narrate as narrate_mod          # noqa: E402
from elevenlabs import (ElevenLabs, ElevenLabsError,  # noqa: E402
                        OUTPUT_FORMATS, default_settings)
import maivoice as mai  # noqa: E402

# static_url_path="" so index.html can reference app.css / assets/ at the root.
app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"),
            static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 200 MB upload ceiling

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
ALLOWED = {".pptx", ".ppt", ".potx"}

# Only needed when the UI is served from another origin (static on Vercel,
# renderer in a container). Empty means same-origin only, the local default.
CORS_ORIGINS = [o.strip().rstrip("/") for o in
                os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]


# A shared token is a speed bump, not authentication: the browser has to know
# it, so it is visible to anyone who views source. It keeps a public URL from
# being trivially crawled and abused. For real access control put an identity
# proxy (Cloudflare Access, Tailscale, an OAuth proxy) in front of this.
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()


@app.before_request
def _gate():
    if not APP_TOKEN or request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    sent = (request.headers.get("X-App-Token")
            or request.args.get("token", "")).strip()
    if sent != APP_TOKEN:
        return jsonify(error="Not authorised."), 401
    return None


@app.after_request
def _cors(resp):
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and ("*" in CORS_ORIGINS or origin in CORS_ORIGINS):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "content-type, x-app-token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


# --- job bookkeeping ---------------------------------------------------------
def new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    with LOCK:
        JOBS[jid] = {"id": jid, "stage": "new", "progress": 0.0, "message": "",
                     "error": None, "deck": None, "created": time.time()}
    (JOBS_DIR / jid).mkdir(parents=True, exist_ok=True)
    return jid


def job(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        abort(404, "Unknown job. It may have expired - re-upload the deck.")
    return j


def set_job(jid: str, **kw) -> None:
    with LOCK:
        if jid in JOBS:
            JOBS[jid].update(kw)


def workdir(jid: str) -> Path:
    return JOBS_DIR / jid


def run_stream(cmd: list[str], on_line, env: dict | None = None) -> int:
    """Run a pipeline script, feeding each stderr line to a progress parser."""
    e = {**os.environ, **(env or {})}
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, env=e, cwd=str(ROOT))
    tail: list[str] = []
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip()
        tail.append(line)
        del tail[:-25]
        on_line(line)
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("\n".join(tail[-8:]) or f"exit {p.returncode}")
    return p.returncode


# --- static ------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/sw.js")
def service_worker():
    # Must be served from the root so its scope covers the whole app, and
    # never cached hard or an update can never land.
    r = send_from_directory(app.static_folder, "sw.js",
                            mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    # Safari probes these at the root even when a <link> is present.
    return send_from_directory(Path(app.static_folder) / "assets",
                               "apple-touch-icon.png", mimetype="image/png")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(Path(app.static_folder) / "assets",
                               "favicon-32.png", mimetype="image/png")


@app.get("/manifest.webmanifest")
def manifest():
    r = send_from_directory(app.static_folder, "manifest.webmanifest",
                            mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/api/health")
def health():
    def have(b):
        return shutil.which(b) is not None
    return jsonify({
        "ffmpeg": have("ffmpeg"), "ffprobe": have("ffprobe"),
        "libreoffice": have("libreoffice") or have("soffice"),
        "pdftoppm": have("pdftoppm"),
        "output_formats": [{"id": i, "label": l} for i, l in OUTPUT_FORMATS],
        "default_voice_settings": default_settings(),
    })


# --- upload + extract --------------------------------------------------------
@app.post("/api/decks")
def upload_deck():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file was uploaded."), 400
    name = secure_filename(f.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        return jsonify(error=f"{ext or 'That file type'} isn't supported. "
                             "Upload a .pptx, .ppt or .potx."), 400

    jid = new_job()
    wd = workdir(jid)
    updir = wd / "upload"          # uploads kept in their own directory
    updir.mkdir(parents=True, exist_ok=True)
    src = updir / name
    f.save(src)
    set_job(jid, stage="extracting", progress=0.05,
            message="Reading the deck", filename=name)

    threading.Thread(target=_extract_worker, args=(jid, src), daemon=True).start()
    return jsonify(job_id=jid), 202


def _extract_worker(jid: str, src: Path) -> None:
    wd = workdir(jid)
    try:
        if src.suffix.lower() == ".ppt":
            set_job(jid, message="Converting .ppt to .pptx", progress=0.1)
            subprocess.run(["libreoffice", "--headless", "--norestore",
                            f"-env:UserInstallation=file://{(wd/'lo').resolve()}",
                            "--convert-to", "pptx", "--outdir",
                            str(src.parent.resolve()), str(src.resolve())],
                           capture_output=True, timeout=600, check=True)
            cand = src.with_suffix(".pptx")
            if cand.exists():
                src = cand

        def on_line(line: str):
            if "slides in" in line:
                set_job(jid, message="Rendering slides", progress=0.2)
            elif "LibreOffice" in line:
                set_job(jid, message="Rendering slides with LibreOffice",
                        progress=0.35)
            elif "rasterising" in line:
                set_job(jid, message="Rasterising pages", progress=0.7)

        run_stream([sys.executable, str(PIPELINE / "extract.py"),
                    str(src.resolve()), "--out", str(wd.resolve())], on_line)

        deck = json.loads((wd / "deck.json").read_text())
        set_job(jid, stage="extracted", progress=1.0, deck=deck,
                message=f"{deck['slide_count']} slides, "
                        f"{deck['slides_with_notes']} with speaker notes")
    except subprocess.TimeoutExpired:
        set_job(jid, stage="error", error="Converting the deck timed out.")
    except Exception as e:
        set_job(jid, stage="error", error=str(e))


@app.get("/api/jobs/<jid>")
def job_status(jid):
    j = dict(job(jid))
    j.pop("eleven_key", None)
    return jsonify(j)


@app.get("/api/jobs/<jid>/slides/<int:n>.png")
def slide_png(jid, n):
    job(jid)
    p = workdir(jid) / "slides" / f"slide-{n:02d}.png"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/png", max_age=3600)


# --- narration ---------------------------------------------------------------
@app.post("/api/jobs/<jid>/narration")
def make_narration(jid):
    j = job(jid)
    if not j.get("deck"):
        return jsonify(error="The deck hasn't finished loading yet."), 409
    body = request.get_json(force=True, silent=True) or {}
    mode = body.get("mode", "basic")
    deck = j["deck"]
    try:
        if mode == "llm":
            key = (body.get("anthropic_key") or "").strip()
            if not key:
                return jsonify(error="Paste an Anthropic API key, or choose "
                                     "the built-in writer."), 400
            nar = narrate_mod.compose_llm(
                deck, key, model=body.get("model") or "claude-opus-4-5",
                tone=body.get("tone", ""))
        else:
            nar = narrate_mod.compose_basic(deck)
    except Exception as e:
        return jsonify(error=str(e)), 502
    (workdir(jid) / "narration.json").write_text(
        json.dumps(nar, indent=2, ensure_ascii=False))
    return jsonify(narration=nar, audit=narrate_mod.audit(nar, deck))


@app.put("/api/jobs/<jid>/narration")
def save_narration(jid):
    j = job(jid)
    body = request.get_json(force=True, silent=True) or {}
    nar = body.get("narration")
    if not isinstance(nar, dict) or "slides" not in nar:
        return jsonify(error="Malformed narration payload."), 400
    (workdir(jid) / "narration.json").write_text(
        json.dumps(nar, indent=2, ensure_ascii=False))
    return jsonify(ok=True, audit=narrate_mod.audit(nar, j.get("deck")))


# --- ElevenLabs --------------------------------------------------------------
@app.post("/api/elevenlabs/connect")
def el_connect():
    body = request.get_json(force=True, silent=True) or {}
    try:
        cl = ElevenLabs(body.get("api_key", ""))
        return jsonify(voices=cl.voices(), models=cl.models(), quota=cl.quota())
    except ElevenLabsError as e:
        return jsonify(error=str(e)), 400


@app.post("/api/elevenlabs/preview")
def el_preview():
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or
            "Here's the picture that started all of this. "
            "Signups are up thirty-four percent year over year.")
    try:
        cl = ElevenLabs(body.get("api_key", ""))
        audio = cl.tts(text[:700], body.get("voice_id", ""),
                       model_id=body.get("model_id") or "eleven_multilingual_v2",
                       settings=body.get("voice_settings") or default_settings(),
                       output_format="mp3_44100_128")
    except ElevenLabsError as e:
        return jsonify(error=str(e)), 400
    return Response(audio, mimetype="audio/mpeg")


# --- MAI-Voice ---------------------------------------------------------------
@app.get("/api/maivoice/catalog")
def mai_catalog():
    return jsonify(mai.catalog())


def _mai_client(body: dict) -> "mai.MaiVoice":
    return mai.MaiVoice(body.get("api_key", ""), body.get("endpoint", ""),
                        body.get("model_id") or mai.DEFAULT_MODEL)


@app.post("/api/maivoice/check")
def mai_check():
    """Validate key + endpoint + voice with the shortest possible synthesis."""
    body = request.get_json(force=True, silent=True) or {}
    try:
        cl = _mai_client(body)
        audio = cl.tts("Hello.", body.get("voice_id") or mai.DEFAULT_VOICE,
                       style=body.get("style") or None,
                       output_format=body.get("output_format")
                       or mai.OUTPUT_FORMATS[0][0])
    except mai.MaiVoiceError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, endpoint=cl.endpoint, model=cl.model,
                   bytes=len(audio))


@app.post("/api/maivoice/preview")
def mai_preview():
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or
            "Here's the picture that started all of this. "
            "Signups are up thirty-four percent year over year.")
    try:
        cl = _mai_client(body)
        audio = cl.tts(text[:900], body.get("voice_id") or mai.DEFAULT_VOICE,
                       style=body.get("style") or None,
                       rate_pct=body.get("rate_pct") or None,
                       output_format=body.get("output_format")
                       or mai.OUTPUT_FORMATS[0][0])
    except mai.MaiVoiceError as e:
        return jsonify(error=str(e)), 400
    fmt = body.get("output_format") or mai.OUTPUT_FORMATS[0][0]
    return Response(audio, mimetype="audio/wav" if fmt.startswith("riff")
                    else "audio/mpeg")


# --- render ------------------------------------------------------------------
@app.post("/api/jobs/<jid>/render")
def render(jid):
    j = job(jid)
    if j.get("stage") in ("synthesizing", "rendering"):
        return jsonify(error="This deck is already rendering."), 409
    if not (workdir(jid) / "narration.json").exists():
        return jsonify(error="Write the narration first."), 409
    body = request.get_json(force=True, silent=True) or {}
    threading.Thread(target=_render_worker, args=(jid, body), daemon=True).start()
    return jsonify(ok=True), 202


def _render_worker(jid: str, opt: dict) -> None:
    wd = workdir(jid)
    try:
        engine = opt.get("engine", "eleven")
        wpm = int(opt.get("wpm", 150))
        env: dict[str, str] = {}

        # Secrets go through the environment; only non-secret settings hit disk.
        cfg_path = wd / "tts_settings.json"
        if engine == "eleven":
            key = (opt.get("api_key") or "").strip()
            if not key:
                raise RuntimeError("No ElevenLabs API key was supplied.")
            env["ELEVENLABS_API_KEY"] = key
            cfg_path.write_text(json.dumps({
                "voice_id": opt.get("voice_id"),
                "voice_name": opt.get("voice_name"),
                "model_id": opt.get("model_id") or "eleven_multilingual_v2",
                "voice_settings": opt.get("voice_settings") or default_settings(),
                "output_format": opt.get("output_format") or "mp3_44100_128",
                "seed": opt.get("seed"),
            }, indent=2))
        elif engine == "mai":
            key = (opt.get("api_key") or "").strip()
            if not key:
                raise RuntimeError("No MAI-Voice API key was supplied.")
            env["MAI_VOICE_KEY"] = key
            cfg_path.write_text(json.dumps({
                "endpoint": opt.get("endpoint"),
                "model_id": opt.get("model_id") or mai.DEFAULT_MODEL,
                "voice_id": opt.get("voice_id") or mai.DEFAULT_VOICE,
                "style": opt.get("style") or None,
                "output_format": opt.get("output_format")
                                 or mai.OUTPUT_FORMATS[0][0],
                "use_prosody": bool(opt.get("use_prosody")),
            }, indent=2))
        else:
            cfg_path.write_text("{}")

        set_job(jid, stage="synthesizing", progress=0.02,
                message="Starting voice synthesis", error=None)

        def on_synth(line: str):
            m = re.search(r"synthesized (\d+)/(\d+)", line)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                set_job(jid, progress=round(a / max(1, b), 3),
                        message=f"Synthesizing narration - cue {a} of {b}")
            elif "mastering" in line:
                set_job(jid, progress=0.97, message="Mastering audio")
            elif "measuring loudness" in line:
                set_job(jid, progress=0.95, message="Measuring loudness")

        cmd = [sys.executable, str(PIPELINE / "synth.py"), str(wd.resolve()),
               "--tts", engine, "--wpm", str(wpm),
               "--tts-settings", str(cfg_path.resolve())]
        for k in ("sentence", "para", "tail", "min_slide"):
            if opt.get(k) is not None:
                cmd += [f"--{k.replace('_', '-')}", str(opt[k])]
        run_stream(cmd, on_synth, env)

        tl = json.loads((wd / "timeline.json").read_text())
        set_job(jid, stage="rendering", progress=0.0,
                message="Rendering video", timeline_total=tl["total"])

        def on_render(line: str):
            m = re.search(r"scene (\d+)/(\d+)", line)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                set_job(jid, progress=round(a / max(1, b), 3),
                        message=f"Rendering scene {a} of {b}")
            elif "burning captions" in line:
                set_job(jid, progress=0.95, message="Burning in captions")
            elif "muxing" in line:
                set_job(jid, progress=0.9, message="Muxing audio and video")

        out = wd / "video.mp4"
        acmd = [sys.executable, str(PIPELINE / "assemble.py"), str(wd.resolve()),
                "--out", str(out.resolve()),
                "--captions", opt.get("captions", "srt"),
                "--zoom", str(opt.get("zoom", 0.035))]
        if opt.get("accent"):
            acmd += ["--accent", opt["accent"]]
        run_stream(acmd, on_render)

        size = out.stat().st_size if out.exists() else 0
        set_job(jid, stage="done", progress=1.0,
                message="Video ready", video_bytes=size,
                duration=tl["total"])
    except Exception as e:
        set_job(jid, stage="error", error=str(e))
    finally:
        (wd / "tts_settings.json").unlink(missing_ok=True)


@app.get("/api/jobs/<jid>/video")
def download_video(jid):
    j = job(jid)
    p = workdir(jid) / "video.mp4"
    if not p.exists():
        abort(404, "No video has been rendered for this deck yet.")
    stem = Path(j.get("filename") or "presentation").stem
    return send_file(p, mimetype="video/mp4", as_attachment=True,
                     download_name=f"{stem}.mp4", conditional=True)


@app.get("/api/jobs/<jid>/captions.srt")
def download_srt(jid):
    j = job(jid)
    p = workdir(jid) / "captions.srt"
    if not p.exists():
        abort(404)
    stem = Path(j.get("filename") or "presentation").stem
    return send_file(p, mimetype="text/plain", as_attachment=True,
                     download_name=f"{stem}.srt")


@app.errorhandler(413)
def too_big(_):
    return jsonify(error="That file is larger than the 200 MB limit."), 413


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8808"))
    print(f"\n  SlideToVideo - deck to video\n  http://{host}:{port}\n")
    app.run(host=host, port=port, threaded=True, debug=False)
