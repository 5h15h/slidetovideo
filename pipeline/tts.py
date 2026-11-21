#!/usr/bin/env python3
"""Pluggable text-to-speech backends.

Every backend takes one narration cue and writes a 48 kHz mono WAV.
Preference order when --tts auto:  azure > edge > piper > say > flite > silent

Azure (Microsoft Foundry / Azure AI Speech) is the quality option. Credentials
come from the environment, never from this file:

    AZURE_SPEECH_KEY     required
    AZURE_SPEECH_REGION  e.g. eastus      (or set AZURE_SPEECH_ENDPOINT)
    AZURE_SPEECH_ENDPOINT  full override, e.g.
        https://my-resource.cognitiveservices.azure.com/cognitiveservices/v1
"""
from __future__ import annotations
import html, os, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import die, ff, log, run, to_ssml_fragment, plain_text  # noqa: F401

SR = 48000

AZURE_REGIONS = [
    "eastus", "eastus2", "westus", "westus2", "westus3", "centralus",
    "southcentralus", "northcentralus", "canadacentral", "brazilsouth",
    "northeurope", "westeurope", "swedencentral", "uksouth", "francecentral",
    "germanywestcentral", "switzerlandnorth", "norwayeast", "polandcentral",
    "italynorth", "spaincentral", "eastasia", "southeastasia", "japaneast",
    "japanwest", "koreacentral", "australiaeast", "centralindia", "uaenorth",
    "southafricanorth",
]

# Warm, unhurried voices that suit deck narration.
AZURE_DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
AZURE_SUGGESTED = [
    "en-US-AndrewMultilingualNeural",   # warm, measured, male
    "en-US-AvaMultilingualNeural",      # bright, natural, female
    "en-US-BrianMultilingualNeural",    # conversational, male
    "en-US-EmmaMultilingualNeural",     # friendly, female
    "en-US-Andrew:DragonHDLatestNeural",
    "en-US-Ava:DragonHDLatestNeural",
    "en-GB-RyanNeural", "en-GB-SoniaNeural",
]


class Backend:
    name = "base"
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError
    def describe(self) -> str:
        return self.name


# --- Azure / Foundry ---------------------------------------------------------
class AzureBackend(Backend):
    name = "azure"

    def __init__(self, voice: str | None, rate_pct: int, style: str | None,
                 key: str | None = None, region: str | None = None,
                 endpoint: str | None = None):
        import requests  # noqa
        self.requests = requests
        self.key = key or os.environ.get("AZURE_SPEECH_KEY", "").strip()
        self.region = (region or os.environ.get("AZURE_SPEECH_REGION", "")).strip()
        self.endpoint = (endpoint or os.environ.get("AZURE_SPEECH_ENDPOINT", "")).strip()
        self.voice = voice or os.environ.get("AZURE_SPEECH_VOICE") or AZURE_DEFAULT_VOICE
        self.rate_pct = rate_pct
        self.style = style
        if not self.key:
            raise RuntimeError("AZURE_SPEECH_KEY is not set")
        if not self.endpoint:
            if not self.region:
                raise RuntimeError(
                    "set AZURE_SPEECH_REGION (or AZURE_SPEECH_ENDPOINT). "
                    "Run `python3 pipeline/tts.py --probe-region` to find it.")
            self.endpoint = (f"https://{self.region}.tts.speech.microsoft.com"
                             "/cognitiveservices/v1")

    def describe(self) -> str:
        where = self.region or self.endpoint
        return f"azure [{self.voice} @ {where}, rate {self.rate_pct:+d}%]"

    def _ssml(self, text: str) -> str:
        body = to_ssml_fragment(text)
        if self.style:
            body = (f'<mstts:express-as style="{html.escape(self.style)}" '
                    f'styledegree="1">{body}</mstts:express-as>')
        lang = "-".join(self.voice.split("-")[:2]) or "en-US"
        return (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="http://www.w3.org/2001/mstts" '
            f'xml:lang="{lang}">'
            f'<voice name="{self.voice}">'
            f'<prosody rate="{self.rate_pct:+d}%">{body}</prosody>'
            '</voice></speak>'
        )

    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-48khz-16bit-mono-pcm",
            "User-Agent": "ppt2video",
        }
        body = self._ssml(text).encode("utf-8")
        delay = 1.5
        last = ""
        for attempt in range(5):
            try:
                r = self.requests.post(self.endpoint, headers=headers,
                                       data=body, timeout=60)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(delay); delay *= 2; continue
            if r.status_code == 200 and r.content[:4] == b"RIFF":
                out.write_bytes(r.content)
                return
            last = f"HTTP {r.status_code} {r.text[:240]}"
            if r.status_code in (429, 500, 502, 503, 504):
                wait = float(r.headers.get("Retry-After", delay))
                log(f"azure {r.status_code}, retrying in {wait:.0f}s")
                time.sleep(wait); delay *= 2; continue
            break
        die("Azure speech request failed: " + last +
            "\n  401/403 -> wrong key or wrong region for this key."
            "\n  Blocked by an egress proxy -> allow *.tts.speech.microsoft.com.")


def probe_region(key: str | None = None) -> None:
    """Find which region a key belongs to by listing voices."""
    import requests
    key = key or os.environ.get("AZURE_SPEECH_KEY", "").strip()
    if not key:
        die("AZURE_SPEECH_KEY is not set")
    print("probing regions (200 = this key's region)")
    for r in AZURE_REGIONS:
        url = f"https://{r}.tts.speech.microsoft.com/cognitiveservices/voices/list"
        try:
            resp = requests.get(url, headers={"Ocp-Apim-Subscription-Key": key},
                                timeout=12)
            code = resp.status_code
        except Exception as e:
            code = f"- ({type(e).__name__})"
        print(f"  {r:<20} {code}")
        if code == 200:
            print(f"\nfound it:  export AZURE_SPEECH_REGION={r}")
            return
    print("\nNo region answered 200. Either the key is not a Speech/AI-Services "
          "key, or outbound HTTPS to *.tts.speech.microsoft.com is blocked.")


# --- edge-tts (free neural, needs network) -----------------------------------
class EdgeBackend(Backend):
    name = "edge"
    def __init__(self, voice: str | None, rate_pct: int):
        self.exe = shutil.which("edge-tts")
        if not self.exe:
            raise RuntimeError("edge-tts not installed")
        self.voice = voice or "en-US-AndrewMultilingualNeural"
        self.rate_pct = rate_pct
    def describe(self) -> str:
        return f"edge-tts [{self.voice}, rate {self.rate_pct:+d}%]"
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        mp3 = out.with_suffix(".mp3")
        run([self.exe, "--voice", self.voice, "--rate",
             f"{self.rate_pct:+d}%", "--text", plain_text(text),
             "--write-media", str(mp3)])
        ff(["-i", str(mp3), "-ac", "1", "-ar", str(SR), str(out)])
        mp3.unlink(missing_ok=True)


# --- piper (local neural) ----------------------------------------------------
class PiperBackend(Backend):
    name = "piper"
    def __init__(self, model: str | None):
        self.exe = shutil.which("piper")
        if not self.exe:
            raise RuntimeError("piper not installed")
        self.model = model or os.environ.get("PIPER_MODEL", "")
        if not self.model or not Path(self.model).exists():
            raise RuntimeError("set PIPER_MODEL to a .onnx voice")
    def describe(self) -> str:
        return f"piper [{Path(self.model).stem}]"
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        raw = out.with_suffix(".piper.wav")
        p = subprocess.run([self.exe, "-m", self.model, "-f", str(raw)],
                           input=plain_text(text), text=True, capture_output=True)
        if p.returncode != 0:
            die(f"piper failed: {p.stderr[:300]}")
        ff(["-i", str(raw), "-ac", "1", "-ar", str(SR), str(out)])
        raw.unlink(missing_ok=True)


# --- macOS say ---------------------------------------------------------------
class SayBackend(Backend):
    name = "say"
    def __init__(self, voice: str | None, wpm: int):
        self.exe = shutil.which("say")
        if not self.exe:
            raise RuntimeError("say not available")
        self.voice = voice or "Samantha"
        self.wpm = wpm
    def describe(self) -> str:
        return f"say [{self.voice}, {self.wpm} wpm]"
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        aiff = out.with_suffix(".aiff")
        run([self.exe, "-v", self.voice, "-r", str(self.wpm),
             "-o", str(aiff), plain_text(text)])
        ff(["-i", str(aiff), "-ac", "1", "-ar", str(SR), str(out)])
        aiff.unlink(missing_ok=True)


# --- ffmpeg flite (always present, dated but real speech) --------------------
class FliteBackend(Backend):
    name = "flite"
    VOICES = ("slt", "awb", "rms", "kal16", "kal")
    NATIVE_WPM = 185
    def __init__(self, voice: str | None, wpm: int = 150):
        r = run(["ffmpeg", "-hide_banner", "-filters"], check=False)
        if "flite" not in r.stdout:
            raise RuntimeError("this ffmpeg has no flite filter")
        self.voice = voice if voice in self.VOICES else "slt"
        # flite has no rate control, so hit the target pace with atempo.
        self.tempo = max(0.6, min(1.6, wpm / self.NATIVE_WPM))
    def describe(self) -> str:
        return (f"flite [{self.voice}, atempo {self.tempo:.2f}] "
                "(offline fallback - robotic)")
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        # textfile= avoids filter-graph escaping problems entirely
        tf = out.with_suffix(".txt")
        tf.write_text(plain_text(text) + "\n")
        af = f"atempo={self.tempo:.4f}" if abs(self.tempo - 1.0) > 0.01 else "anull"
        ff(["-f", "lavfi", "-i", f"flite=textfile={tf}:voice={self.voice}",
            "-af", af, "-ac", "1", "-ar", str(SR), str(out)])
        tf.unlink(missing_ok=True)


# --- ElevenLabs --------------------------------------------------------------
class ElevenBackend(Backend):
    name = "eleven"

    def __init__(self, voice: str | None, cfg: dict | None = None):
        from elevenlabs import ElevenLabs, ElevenLabsError, default_settings
        cfg = cfg or {}
        key = cfg.get("api_key") or os.environ.get("ELEVENLABS_API_KEY", "")
        if not key.strip():
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        self.client = ElevenLabs(key)
        self.err = ElevenLabsError
        self.voice_id = (cfg.get("voice_id") or voice
                         or os.environ.get("ELEVENLABS_VOICE_ID", ""))
        if not self.voice_id:
            raise RuntimeError("no ElevenLabs voice selected")
        self.model_id = (cfg.get("model_id")
                         or os.environ.get("ELEVENLABS_MODEL_ID")
                         or "eleven_multilingual_v2")
        self.settings = {**default_settings(), **(cfg.get("voice_settings") or {})}
        self.output_format = cfg.get("output_format") or "mp3_44100_128"
        self.seed = cfg.get("seed")
        self.voice_name = cfg.get("voice_name") or self.voice_id[:8]

    def describe(self) -> str:
        v = self.settings
        return (f"elevenlabs [{self.voice_name}, {self.model_id}, "
                f"stability {v.get('stability')}, similarity "
                f"{v.get('similarity_boost')}, style {v.get('style')}, "
                f"speed {v.get('speed')}]")

    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        try:
            audio = self.client.tts(
                plain_text(text), self.voice_id, model_id=self.model_id,
                settings=self.settings, previous_text=prev, next_text=nxt,
                output_format=self.output_format, seed=self.seed)
        except self.err as e:
            die(str(e))
            return
        ext = ".pcm" if self.output_format.startswith("pcm") else ".mp3"
        raw = out.with_suffix(ext)
        raw.write_bytes(audio)
        if ext == ".pcm":
            rate = self.output_format.split("_")[1]
            ff(["-f", "s16le", "-ar", rate, "-ac", "1", "-i", str(raw),
                "-ac", "1", "-ar", str(SR), str(out)])
        else:
            ff(["-i", str(raw), "-ac", "1", "-ar", str(SR), str(out)])
        raw.unlink(missing_ok=True)


# --- MAI-Voice (Microsoft AI, over Azure Speech) -----------------------------
class MaiBackend(Backend):
    name = "mai"

    def __init__(self, voice: str | None, cfg: dict | None = None,
                 rate_pct: int = 0):
        from maivoice import (MaiVoice, MaiVoiceError, DEFAULT_MODEL,
                              DEFAULT_VOICE, OUTPUT_FORMATS)
        cfg = cfg or {}
        self.err = MaiVoiceError
        self.model = cfg.get("model_id") or os.environ.get("MAI_VOICE_MODEL") or DEFAULT_MODEL
        self.client = MaiVoice(cfg.get("api_key", ""), cfg.get("endpoint", ""),
                               self.model)
        self.voice_id = (cfg.get("voice_id") or voice
                         or os.environ.get("MAI_VOICE_ID") or DEFAULT_VOICE)
        self.style = cfg.get("style") or None
        self.output_format = cfg.get("output_format") or OUTPUT_FORMATS[0][0]
        # MAI paces itself; prosody is opt-in because an expressive model
        # reads better when it is not being pushed.
        self.rate_pct = rate_pct if cfg.get("use_prosody") else 0

    def describe(self) -> str:
        bits = [self.voice_id, self.model]
        if self.style:
            bits.append(f"style {self.style}")
        if self.rate_pct:
            bits.append(f"rate {self.rate_pct:+d}%")
        return "mai-voice [" + ", ".join(bits) + "]"

    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        try:
            audio = self.client.tts(text, self.voice_id, style=self.style,
                                    rate_pct=self.rate_pct or None,
                                    output_format=self.output_format)
        except self.err as e:
            die(str(e))
            return
        if self.output_format.startswith("riff"):
            raw = out.with_suffix(".riff.wav")
            raw.write_bytes(audio)
            ff(["-i", str(raw), "-ac", "1", "-ar", str(SR), str(out)])
        else:
            raw = out.with_suffix(".mp3")
            raw.write_bytes(audio)
            ff(["-i", str(raw), "-ac", "1", "-ar", str(SR), str(out)])
        raw.unlink(missing_ok=True)


# --- silent (pacing preview with no TTS at all) ------------------------------
class SilentBackend(Backend):
    name = "silent"
    def __init__(self, wpm: int):
        self.wpm = max(60, wpm)
    def describe(self) -> str:
        return f"silent [timed at {self.wpm} wpm - no voice]"
    def synth(self, text: str, out: Path, prev: str | None = None,
              nxt: str | None = None) -> None:
        words = max(1, len(plain_text(text).split()))
        secs = max(0.6, words / self.wpm * 60.0)
        ff(["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono",
            "-t", f"{secs:.3f}", str(out)])


def build(kind: str, voice: str | None, wpm: int, style: str | None = None,
          cfg: dict | None = None) -> Backend:
    """wpm is converted to a percentage offset from each engine's own default."""
    rate_pct = int(round((wpm / 180.0 - 1.0) * 100))
    rate_pct = max(-40, min(40, rate_pct))
    order = ([kind] if kind != "auto"
             else ["eleven", "mai", "azure", "edge", "piper", "say",
                   "flite", "silent"])
    errors = []
    for k in order:
        try:
            if k == "eleven": return ElevenBackend(voice, cfg)
            if k == "mai":    return MaiBackend(voice, cfg, rate_pct)
            if k == "azure":  return AzureBackend(voice, rate_pct, style)
            if k == "edge":   return EdgeBackend(voice, rate_pct)
            if k == "piper":  return PiperBackend(voice)
            if k == "say":    return SayBackend(voice, wpm)
            if k == "flite":  return FliteBackend(voice, wpm)
            if k == "silent": return SilentBackend(wpm)
            errors.append(f"{k}: unknown backend")
        except Exception as e:
            errors.append(f"{k}: {e}")
    die("no usable TTS backend:\n   " + "\n   ".join(errors))
    raise SystemExit(1)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="TTS backend utilities")
    ap.add_argument("--probe-region", action="store_true",
                    help="find the Azure region for AZURE_SPEECH_KEY")
    ap.add_argument("--list-voices", action="store_true",
                    help="list Azure voices for the configured region")
    ap.add_argument("--say", help="synthesize one line to /tmp/tts-test.wav")
    ap.add_argument("--tts", default="auto")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--wpm", type=int, default=150)
    ap.add_argument("--style", default=None)
    a = ap.parse_args()
    if a.probe_region:
        probe_region()
    elif a.list_voices:
        import requests, json as _j
        region = os.environ.get("AZURE_SPEECH_REGION", "eastus")
        r = requests.get(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list",
            headers={"Ocp-Apim-Subscription-Key": os.environ.get("AZURE_SPEECH_KEY", "")},
            timeout=30)
        if r.status_code != 200:
            die(f"HTTP {r.status_code}: {r.text[:200]}")
        for v in _j.loads(r.text):
            if v.get("Locale", "").startswith("en"):
                print(f"{v['ShortName']:<44} {v.get('Gender',''):<7} {v.get('Locale','')}")
    elif a.say:
        b = build(a.tts, a.voice, a.wpm, a.style)
        print(f"backend: {b.describe()}")
        out = Path("/tmp/tts-test.wav"); b.synth(a.say, out)
        print(f"wrote {out}")
    else:
        ap.print_help()
