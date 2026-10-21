from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx

COHERE_CHAT_URL = "https://api.cohere.ai/v2/chat"
DEFAULT_VISION_MODEL = "command-a-vision-07-2025"


class CohereError(RuntimeError):
    pass


def get_cohere_api_key(explicit: str | None = None) -> str:
    key = (
        explicit
        or os.environ.get("COHERE_API_KEY")
        or os.environ.get("CO_API_KEY")
    )
    if not key:
        raise CohereError(
            "COHERE_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return key


def get_vision_model(explicit: str | None = None) -> str:
    return (
        explicit
        or os.environ.get("COHERE_VISION_MODEL")
        or os.environ.get("COHERE_ENRICH_MODEL")
        or DEFAULT_VISION_MODEL
    )


def png_data_url(png_bytes: bytes, *, detail: str = "high") -> dict:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{b64}",
            "detail": detail,
        },
    }


def path_data_url(path: Path, *, detail: str = "high") -> dict:
    return png_data_url(path.read_bytes(), detail=detail)


def chat_json(
    *,
    content: list[dict],
    api_key: str | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    timeout: float = 180.0,
) -> dict:
    key = get_cohere_api_key(api_key)
    payload = {
        "model": get_vision_model(model),
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            COHERE_CHAT_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise CohereError(
                f"Cohere API error {response.status_code}: {response.text[:500]}"
            )
        body = response.json()

    raw = _extract_text(body)
    return _parse_json_payload(raw)


def _extract_text(body: dict) -> str:
    message = body.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                chunks.append(block)
        return "\n".join(chunks).strip()
    if "text" in body:
        return str(body["text"])
    raise CohereError(f"Unexpected Cohere response shape: {list(body.keys())}")


def _parse_json_payload(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise CohereError(f"Cohere did not return JSON: {raw[:300]}")
        return json.loads(match.group(0))
