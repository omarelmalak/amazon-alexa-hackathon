"""ElevenLabs TTS — Flash model (cheapest) + default female voice."""

from __future__ import annotations

import os

import httpx

# Premade Sarah (young female) — available on free API (library voices like Rachel are paid-only).
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
# Cheapest / fastest API model (~$0.04 / 1K chars).
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
# Low-bitrate MP3 keeps download light; char pricing is model-based.
DEFAULT_OUTPUT_FORMAT = "mp3_22050_32"

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
VOICES_URL = "https://api.elevenlabs.io/v1/voices"

_cached_voice_id: str | None = None


class ElevenLabsError(RuntimeError):
    pass


def get_elevenlabs_api_key(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("ELEVENLABS_API_KEY") or None


def elevenlabs_configured() -> bool:
    return bool(get_elevenlabs_api_key())


def _resolve_voice_id(client: httpx.Client, key: str, preferred: str | None) -> str:
    global _cached_voice_id
    if preferred:
        return preferred
    if _cached_voice_id:
        return _cached_voice_id

    env_voice = os.environ.get("ELEVENLABS_VOICE_ID")
    if env_voice:
        _cached_voice_id = env_voice
        return env_voice

    # Prefer Sarah; otherwise first premade female on this account.
    response = client.get(VOICES_URL, headers={"xi-api-key": key})
    if response.status_code >= 400:
        _cached_voice_id = DEFAULT_VOICE_ID
        return DEFAULT_VOICE_ID

    voices = response.json().get("voices") or []
    for v in voices:
        if v.get("voice_id") == DEFAULT_VOICE_ID:
            _cached_voice_id = DEFAULT_VOICE_ID
            return DEFAULT_VOICE_ID
    for v in voices:
        labels = v.get("labels") or {}
        if str(labels.get("gender", "")).lower() == "female":
            _cached_voice_id = str(v["voice_id"])
            return _cached_voice_id

    _cached_voice_id = DEFAULT_VOICE_ID
    return DEFAULT_VOICE_ID


def synthesize_speech(
    text: str,
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
    model_id: str | None = None,
    timeout: float = 60.0,
) -> bytes:
    key = get_elevenlabs_api_key(api_key)
    if not key:
        raise ElevenLabsError("ELEVENLABS_API_KEY not set")

    cleaned = (text or "").strip()
    if not cleaned:
        raise ElevenLabsError("Empty text")

    model = model_id or os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL_ID

    with httpx.Client(timeout=timeout) as client:
        voice = _resolve_voice_id(client, key, voice_id)
        url = TTS_URL.format(voice_id=voice)
        response = client.post(
            url,
            headers={
                "xi-api-key": key,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            params={"output_format": DEFAULT_OUTPUT_FORMAT},
            json={
                "text": cleaned,
                "model_id": model,
                "voice_settings": {
                    "stability": 0.45,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": False,
                },
            },
        )
        if response.status_code >= 400:
            raise ElevenLabsError(
                f"ElevenLabs {response.status_code}: {response.text[:400]}"
            )
        return response.content
