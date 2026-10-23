"""Cohere Chat v2 tool-calling helpers for the MCP agent host."""

from __future__ import annotations

import json
from typing import Any

import httpx

from infra.cohere_client import (
    COHERE_CHAT_URL,
    CohereError,
    get_cohere_api_key,
    get_text_model,
)


def chat_with_tools(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Call Cohere Chat v2 with tools; return the raw response JSON."""
    key = get_cohere_api_key(api_key)
    msgs = list(messages)
    if system:
        # Prefer an explicit system message at the front if not already present.
        if not msgs or msgs[0].get("role") != "system":
            msgs = [{"role": "system", "content": system}, *msgs]

    payload: dict[str, Any] = {
        "model": get_text_model(model),
        "messages": msgs,
        "tools": tools,
        "temperature": temperature,
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
                f"Cohere API error {response.status_code}: {response.text[:800]}"
            )
        return response.json()


def extract_assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    message = body.get("message") or {}
    if not isinstance(message, dict):
        raise CohereError("Cohere response missing message object")
    return message


def extract_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p).strip()
    return ""


def extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("tool_calls") or []
    if not isinstance(raw, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") or {}
        name = fn.get("name") or item.get("name")
        if not name:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(
            {
                "id": item.get("id") or f"call_{len(calls)}",
                "name": str(name),
                "arguments": args,
                "raw": item,
            }
        )
    return calls


def tool_result_message(tool_call_id: str, content: Any) -> dict[str, Any]:
    if isinstance(content, (dict, list)):
        text = json.dumps(content, ensure_ascii=True, default=str)
    else:
        text = str(content)
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": text,
    }
