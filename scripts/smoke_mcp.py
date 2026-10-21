#!/usr/bin/env python3
"""Smoke-test Streamable HTTP MCP tools against a running server.

Usage (server already up on :8000):
  PYTHONPATH=src python scripts/smoke_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = "http://127.0.0.1:8000/mcp"


async def main() -> int:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("tools:", ", ".join(names))

            result = await session.call_tool("list_products", {})
            print("list_products:", _text(result))

            result = await session.call_tool(
                "get_assembly_step",
                {"product_id": "kyrre", "step_number": 1},
            )
            print("get_assembly_step:", _text(result))

            result = await session.call_tool(
                "create_session", {"product_id": "kyrre"}
            )
            print("create_session:", _text(result))
    return 0


def _text(result: object) -> str:
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts and hasattr(result, "structuredContent"):
        return json.dumps(result.structuredContent, indent=2)
    raw = "\n".join(parts) if parts else str(result)
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (json.JSONDecodeError, TypeError):
        return raw


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print(f"smoke_mcp failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
