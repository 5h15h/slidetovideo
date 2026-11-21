#!/usr/bin/env python3
"""MAI-Voice (Microsoft AI) text to speech.

MAI-Voice is served through Azure Speech: an SSML POST to the
`cognitiveservices/v1` endpoint of a Speech resource, with the voice named
`<VoiceID>:<ModelSuffix>` — for example `en-US-Harper:MAI-Voice-2.1`.

Credentials come from the caller or the environment and are never stored here:

    MAI_VOICE_KEY       Speech resource key
    MAI_VOICE_ENDPOINT  full endpoint URL, or just a region name
    MAI_VOICE_MODEL     default model suffix
"""
from __future__ import annotations
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import to_ssml_fragment

TIMEOUT = 90

MODELS = [
    {"id": "MAI-Voice-2.1",
     "name": "MAI-Voice-2.1",
     "note": "Highest fidelity, most expressive. Best for narration."},
    {"id": "MAI-Voice-2.1-Flash",
     "name": "MAI-Voice-2.1-Flash",
     "note": "Low latency, optimised for responsiveness."},
    {"id": "MAI-Voice-2",
     "name": "MAI-Voice-2 (legacy)",
     "note": "The June 2026 model. Being retired — prefer 2.1."},
]

# Regions that serve MAI-Voice.
REGIONS = ["eastus", "eastus2", "westus", "westus2", "westus3", "canadacentral",
           "northeurope", "westeurope", "swedencentral", "francecentral",
           "centralindia", "eastasia", "southeastasia", "japaneast"]

# Voice IDs by locale. The model suffix is appended at synthesis time.
VOICES: dict[str, list[str]] = {
    "cs-CZ": ["Grant", "Harper"],
    "da-DK": ["Grant", "Harper"],
    "de-DE": ["Grant", "Harper", "Klaus", "Mia"],
    "en-AU": ["Isla"],
    "en-GB": ["Emily", "Harry"],
    "en-IN": ["Dhruv", "Priya"],
    "en-US": ["Ethan", "Grant", "Harper", "Iris", "Jasper", "Olivia", "Sage"],
    "es-ES": ["Marta"],
    "es-MX": ["Alejo", "Grant", "Harper", "Valeria"],
    "fi-FI": ["Grant", "Harper"],
    "fr-FR": ["Grant", "Harper", "Marc", "Soleil"],
    "hi-IN": ["Arjun", "Dhruv", "Grant", "Harper", "Kavya", "Priya"],
    "hu-HU": ["Bence", "Grant", "Harper", "Levente", "Lilla", "Reka"],
    "id-ID": ["Grant", "Harper"],
    "it-IT": ["Grant", "Harper", "Luca", "Rosa"],
    "ko-KR": ["Grant", "Haena", "Harper", "Junho"],
    "nb-NO": ["Grant", "Harper"],
    "nl-NL": ["Grant", "Harper", "Sander"],
    "pl-PL": ["Grant", "Harper"],
    "pt-BR": ["Caio", "Grant", "Harper", "Luana", "Pedro", "Rafael"],
    "pt-PT": ["Grant", "Harper", "Rui"],
    "ro-RO": ["Andrei", "Elena", "Grant", "Harper", "Ioana", "Radu"],
    "ru-RU": ["Grant", "Harper", "Lev", "Masha"],
    "sv-SE": ["Grant", "Harper"],
    "th-TH": ["Grant", "Harper", "Krit", "Nattapong"],
    "tr-TR": ["Aydin", "Elif", "Grant", "Harper"],
    "vi-VN": ["Grant", "Harper"],
    "zh-CN": ["Bo", "Grant", "Harper", "Lan", "Mei", "Wei"],
}

# Styles are per-voice. Microsoft documents these three explicitly; for any
# other voice the UI offers free text, because sending a style a voice does
# not support is an error rather than a no-op.
STYLES: dict[str, list[str]] = {
    "en-US-Harper": ["agent", "angry", "audiobook", "confused",
                     "customer_call_center", "determined", "educational",
                     "embarrassed", "excited", "happy", "hopeful", "joyful",
                     "narrator", "neutral", "regretful", "relieved", "sad",
                     "shouting", "softvoice", "whispering"],
    "en-US-Grant": ["agent", "audiobook", "customer_call_center",
                    "educational", "narrator", "neutral"],
    "en-US-Jasper": ["neutral"],
}

OUTPUT_FORMATS = [
    ("audio-24khz-160kbitrate-mono-mp3", "MP3 24 kHz / 160 kbps - documented default"),
    ("riff-24khz-16bit-mono-pcm", "PCM 24 kHz - uncompressed"),
]

DEFAULT_MODEL = "MAI-Voice-2.1"
DEFAULT_VOICE = "en-US-Harper"


class MaiVoiceError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def catalog() -> dict:
    return {
        "models": MODELS, "regions": REGIONS, "styles": STYLES,
        "default_model": DEFAULT_MODEL, "default_voice": DEFAULT_VOICE,
        "output_formats": [{"id": i, "label": l} for i, l in OUTPUT_FORMATS],
        "locales": [{"locale": loc, "voices": names}
                    for loc, names in VOICES.items()],
    }


def resolve_endpoint(endpoint: str) -> str:
    """Accept a full URL, a bare hostname, or just a region name."""
    e = (endpoint or "").strip()
    if not e:
        raise MaiVoiceError("No MAI-Voice endpoint or region was given.")
    if re.fullmatch(r"[a-z0-9]+", e):            # a region name
        return f"https://{e}.tts.speech.microsoft.com/cognitiveservices/v1"
    if not e.startswith(("http://", "https://")):
        e = "https://" + e
    if e.startswith("http://"):
        raise MaiVoiceError("Use an https endpoint - the key is sent as a header.")
    e = e.rstrip("/")
    if not e.endswith("/cognitiveservices/v1"):
        e += "/cognitiveservices/v1"
    return e


def voice_name(voice_id: str, model: str) -> str:
    """`en-US-Harper` + `MAI-Voice-2.1` -> `en-US-Harper:MAI-Voice-2.1`."""
    v = (voice_id or DEFAULT_VOICE).strip()
    return v if ":" in v else f"{v}:{(model or DEFAULT_MODEL).strip()}"


def locale_of(voice_id: str) -> str:
    m = re.match(r"([a-z]{2}-[A-Z]{2})", voice_id or "")
    return m.group(1) if m else "en-US"


def build_ssml(text: str, voice_id: str, model: str, style: str | None = None,
               rate_pct: int | None = None) -> str:
    body = to_ssml_fragment(text)
    # Microsoft's example nests the text inside express-as; prosody goes within.
    if rate_pct:
        body = f'<prosody rate="{rate_pct:+d}%">{body}</prosody>'
    if style:
        body = f'<mstts:express-as style="{style}">{body}</mstts:express-as>'
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" '
        f'xml:lang="{locale_of(voice_id)}">'
        f'<voice name="{voice_name(voice_id, model)}">{body}</voice></speak>'
    )


class MaiVoice:
    def __init__(self, api_key: str = "", endpoint: str = "",
                 model: str = DEFAULT_MODEL):
        self.key = (api_key or os.environ.get("MAI_VOICE_KEY", "")).strip()
        if not self.key:
            raise MaiVoiceError("No MAI-Voice API key supplied.")
        self.endpoint = resolve_endpoint(
            endpoint or os.environ.get("MAI_VOICE_ENDPOINT", ""))
        self.model = model or os.environ.get("MAI_VOICE_MODEL") or DEFAULT_MODEL
        self.s = requests.Session()

    def tts(self, text: str, voice_id: str, *, style: str | None = None,
            rate_pct: int | None = None,
            output_format: str = OUTPUT_FORMATS[0][0]) -> bytes:
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": output_format,
            "User-Agent": "ppt2video",
        }
        ssml = build_ssml(text, voice_id, self.model, style, rate_pct).encode("utf-8")
        delay, last = 2.0, ""
        for _ in range(4):
            try:
                r = self.s.post(self.endpoint, headers=headers, data=ssml,
                                timeout=TIMEOUT)
            except requests.RequestException as e:
                last = (f"Could not reach the MAI-Voice endpoint "
                        f"({type(e).__name__}). Check the URL, and whether an "
                        f"egress proxy allows {self.endpoint.split('/')[2]}.")
                time.sleep(delay); delay *= 2; continue
            if r.status_code == 200 and r.content:
                return r.content
            last = _explain(r, self.model)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After", delay)))
                delay *= 2; continue
            raise MaiVoiceError(last, r.status_code)
        raise MaiVoiceError(last or "MAI-Voice request failed repeatedly.")


def _explain(r: requests.Response, model: str) -> str:
    body = (r.text or "")[:300].strip()
    if r.status_code == 401:
        return "MAI-Voice rejected the key (401). Check the key and that it belongs to this endpoint's resource."
    if r.status_code == 403:
        return f"MAI-Voice refused the request (403). The resource may not be enabled for {model}. {body}"
    if r.status_code == 400:
        return ("MAI-Voice rejected the SSML (400). Usually an unsupported voice, "
                f"model suffix or style for this voice. {body}")
    if r.status_code == 404:
        return "Endpoint not found (404). Check the region or custom endpoint URL."
    if r.status_code == 429:
        return "MAI-Voice rate limit hit (429). Wait a moment and retry."
    return f"MAI-Voice error {r.status_code}. {body}"
