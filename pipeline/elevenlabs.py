#!/usr/bin/env python3
"""ElevenLabs REST client.

Only the parts the app needs: list voices and models, read the character
quota, synthesize one cue. Keys are passed in by the caller and never stored
on disk by this module.
"""
from __future__ import annotations
import time
from typing import Any

import requests

BASE = "https://api.elevenlabs.io"
TIMEOUT = 90

# Used only when /v1/models cannot be reached; the app prefers the live list.
FALLBACK_MODELS = [
    {"model_id": "eleven_multilingual_v2",
     "name": "Multilingual v2", "note": "Highest quality. Best default for narration."},
    {"model_id": "eleven_turbo_v2_5",
     "name": "Turbo v2.5", "note": "Faster, slightly less expressive."},
    {"model_id": "eleven_flash_v2_5",
     "name": "Flash v2.5", "note": "Fastest and cheapest."},
]

OUTPUT_FORMATS = [
    ("mp3_44100_128", "MP3 44.1 kHz / 128 kbps - works on every plan"),
    ("mp3_44100_192", "MP3 44.1 kHz / 192 kbps - needs Creator or above"),
    ("pcm_44100", "PCM 44.1 kHz - uncompressed, needs Pro or above"),
]


class ElevenLabsError(RuntimeError):
    """Carries a message already fit to show a user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _explain(r: requests.Response) -> str:
    try:
        body = r.json()
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            msg = detail.get("message") or detail.get("status") or str(detail)
        elif isinstance(detail, list) and detail:
            d0 = detail[0]
            msg = d0.get("msg", str(d0)) if isinstance(d0, dict) else str(d0)
        else:
            msg = str(detail)
    except Exception:
        msg = (r.text or "")[:300]
    if r.status_code == 401:
        return "ElevenLabs rejected the API key (401). Check it was pasted in full."
    if r.status_code == 403:
        return f"ElevenLabs refused this request (403). {msg}"
    if r.status_code == 422:
        return f"ElevenLabs rejected the settings (422). {msg}"
    if r.status_code == 429:
        return "ElevenLabs rate limit hit (429). Wait a moment and retry."
    return f"ElevenLabs error {r.status_code}. {msg}"


class ElevenLabs:
    def __init__(self, api_key: str):
        key = (api_key or "").strip()
        if not key:
            raise ElevenLabsError("No ElevenLabs API key supplied.")
        self.key = key
        self.s = requests.Session()
        self.s.headers.update({"xi-api-key": key, "User-Agent": "ppt2video"})

    # --- reads ---------------------------------------------------------------
    def _get(self, path: str) -> Any:
        try:
            r = self.s.get(f"{BASE}{path}", timeout=TIMEOUT)
        except requests.RequestException as e:
            raise ElevenLabsError(
                f"Could not reach ElevenLabs ({type(e).__name__}). "
                "Check connectivity, or whether an egress proxy allows "
                "api.elevenlabs.io.") from e
        if r.status_code != 200:
            raise ElevenLabsError(_explain(r), r.status_code)
        return r.json()

    def voices(self) -> list[dict]:
        data = self._get("/v1/voices")
        out = []
        for v in data.get("voices", []):
            labels = v.get("labels") or {}
            out.append({
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "category": v.get("category"),
                "description": v.get("description") or "",
                "preview_url": v.get("preview_url"),
                "labels": [str(x) for x in labels.values() if x][:4],
            })
        out.sort(key=lambda v: (v["category"] != "cloned", v["name"] or ""))
        return out

    def models(self) -> list[dict]:
        try:
            data = self._get("/v1/models")
        except ElevenLabsError:
            return FALLBACK_MODELS
        out = []
        for m in data:
            if not m.get("can_do_text_to_speech", True):
                continue
            out.append({"model_id": m.get("model_id"), "name": m.get("name"),
                        "note": (m.get("description") or "")[:140]})
        return out or FALLBACK_MODELS

    def quota(self) -> dict:
        try:
            s = self._get("/v1/user/subscription")
        except ElevenLabsError:
            return {}
        used = s.get("character_count")
        limit = s.get("character_limit")
        return {"used": used, "limit": limit, "tier": s.get("tier"),
                "remaining": (limit - used) if (isinstance(used, int)
                                                and isinstance(limit, int)) else None}

    # --- synthesis -----------------------------------------------------------
    def tts(self, text: str, voice_id: str, *, model_id: str,
            settings: dict | None = None, previous_text: str | None = None,
            next_text: str | None = None, output_format: str = "mp3_44100_128",
            seed: int | None = None) -> bytes:
        if not voice_id:
            raise ElevenLabsError("No ElevenLabs voice selected.")
        body: dict[str, Any] = {"text": text, "model_id": model_id}
        if settings:
            body["voice_settings"] = settings
        # Continuity: telling the model what came before and after keeps
        # prosody consistent when narration is synthesized sentence by sentence.
        if previous_text:
            body["previous_text"] = previous_text[-600:]
        if next_text:
            body["next_text"] = next_text[:600]
        if seed is not None:
            body["seed"] = int(seed)

        url = f"{BASE}/v1/text-to-speech/{voice_id}"
        delay = 2.0
        last = ""
        for _ in range(4):
            try:
                r = self.s.post(url, json=body,
                                params={"output_format": output_format},
                                timeout=TIMEOUT)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(delay); delay *= 2; continue
            if r.status_code == 200 and r.content:
                return r.content
            last = _explain(r)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After", delay)))
                delay *= 2; continue
            raise ElevenLabsError(last, r.status_code)
        raise ElevenLabsError(last or "ElevenLabs request failed repeatedly.")


def default_settings() -> dict:
    """Tuned for narration rather than conversational read."""
    return {"stability": 0.45, "similarity_boost": 0.75, "style": 0.10,
            "use_speaker_boost": True, "speed": 1.0}
