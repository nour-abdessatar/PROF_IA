"""
tts.py — async ElevenLabs text-to-speech client.

Uses the non-streaming "with-timestamps" endpoint: one request per turn, called
from stream_handler.py on the full, post-processed text. The character-level
alignment it returns lets the frontend sync widget steps and word highlighting
to the audio.

Environment variables (both required, otherwise TTS is skipped):
    ELEVENLABS_API_KEY
    ELEVENLABS_VOICE_ID   (eleven_multilingual_v2 reads Arabic and French with one voice)

    audio = await synthesize_with_timestamps(text, lang="ar")
    # -> {"audio_base64": "...", "alignment": {...}} or None
"""

from __future__ import annotations

import os
import httpx

ELEVEN_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID       = os.environ.get("ELEVENLABS_VOICE_ID", "")

_TTS_URL_TMPL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
_TIMEOUT_SECONDS = 60


async def synthesize_with_timestamps(text: str, lang: str = "ar") -> dict | None:
    """
    Call ElevenLabs and return
        {"audio_base64": "<mp3, base64>",
         "alignment": {"characters": [...],
                       "character_start_times_seconds": [...],
                       "character_end_times_seconds": [...]}}
    or None if the text is empty or the call fails for any reason.

    Never raises: TTS is a best-effort add-on and must not block delivery of the
    widget / teacher text.
    """
    text = (text or "").strip()
    if not text:
        return None

    if not ELEVEN_API_KEY or not VOICE_ID:
        print("[tts] ⚠️ ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not set — skipping TTS")
        return None

    url = _TTS_URL_TMPL.format(voice_id=VOICE_ID)
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"[tts] ⚠️ ElevenLabs request failed ({lang}): {exc!r}")
        return None

    alignment = data.get("normalized_alignment") or data.get("alignment")
    audio_b64 = data.get("audio_base64")
    if not audio_b64:
        print("[tts] ⚠️ ElevenLabs response had no audio_base64 — skipping")
        return None

    return {"audio_base64": audio_b64, "alignment": alignment}