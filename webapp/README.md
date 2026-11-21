# webapp

Flask app serving `static/` and a small JSON API. Renders run in background
threads; the browser polls `/api/jobs/<id>`.

| Route | Method | Purpose |
|---|---|---|
| `/api/health` | GET | which tools are installed, output formats |
| `/api/decks` | POST | upload a deck, returns `job_id` |
| `/api/jobs/<id>` | GET | stage, progress, message, deck, error |
| `/api/jobs/<id>/slides/<n>.png` | GET | slide still |
| `/api/jobs/<id>/narration` | POST | draft (`mode: basic\|llm`) |
| `/api/jobs/<id>/narration` | PUT | save edits, returns audit |
| `/api/elevenlabs/connect` | POST | voices, models, quota for a key |
| `/api/elevenlabs/preview` | POST | one-line preview, returns audio/mpeg |
| `/api/maivoice/catalog` | GET | models, regions, 97 voices, per-voice styles |
| `/api/maivoice/check` | POST | one-word synthesis to validate key + endpoint |
| `/api/maivoice/preview` | POST | one-line preview, returns audio |
| `/api/jobs/<id>/render` | POST | start a render |
| `/api/jobs/<id>/video` | GET | finished MP4 |
| `/api/jobs/<id>/captions.srt` | GET | captions |

Keys arrive per-request and are never persisted. For a render the provider key
(`ELEVENLABS_API_KEY` or `MAI_VOICE_KEY`) is passed to the pipeline subprocess
through its environment, while only non-secret settings — voice, model,
endpoint, slider values — are written to the job's `tts_settings.json`, which is
deleted when the render finishes.

A MAI-Voice endpoint is user-supplied, so `maivoice.resolve_endpoint` rejects
plain HTTP: the key travels as a request header.
