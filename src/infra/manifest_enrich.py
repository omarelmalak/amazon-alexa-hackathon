from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import httpx

from core.models.catalog import ProductManifest

COHERE_CHAT_URL = "https://api.cohere.ai/v2/chat"
DEFAULT_VISION_MODEL = "command-a-vision-07-2025"


class EnrichmentError(RuntimeError):
    pass


def enrich_manifest_with_vision(
    product_dir: Path,
    manifest: dict,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    """
    Fill part names, step titles, and completionHints using Cohere Command A Vision.

    Sends each diagram PNG as a base64 data URL (data:image/png;base64,...).
    Cohere vision models understand these images (diagrams, OCR of part numbers).

    Requires COHERE_API_KEY or CO_API_KEY (or api_key=).
    """
    key = (
        api_key
        or os.environ.get("COHERE_API_KEY")
        or os.environ.get("CO_API_KEY")
    )
    model = model or os.environ.get("COHERE_ENRICH_MODEL") or DEFAULT_VISION_MODEL
    if not key:
        raise EnrichmentError(
            "COHERE_API_KEY not set — skipping LLM enrichment. "
            "Copy .env.example to .env, set the key, or pass enrich=false."
        )

    product_dir = product_dir.resolve()
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "You are curating ground truth for an IKEA assembly co-pilot. "
                "Given the BOM diagram and each step diagram, return JSON that "
                "enriches the scaffolded manifest.\n\n"
                "Rules:\n"
                "- Keep all partId and qty values unchanged when present in the scaffold.\n"
                "- If the scaffold is missing hardware that is clearly labeled on a "
                "diagram (6-digit part numbers + Nx quantities), include them.\n"
                "- Give short English hardware names (e.g. 'hex socket screw', 'allen key').\n"
                "- Give a concise step title describing the physical action.\n"
                "- completionHints: 2-4 observable checks a camera/inspector could verify "
                "when the step is done (orientation, flush fasteners, part placement).\n"
                "- Respond with ONLY JSON matching this schema:\n"
                '{ "bom": [{"partId": str, "name": str, "qty": int}], '
                '"steps": [{"number": int, "title": str, '
                '"requiredHardware": [{"partId": str, "name": str, "qty": int}], '
                '"completionHints": [str]}] }\n\n'
                f"Current scaffold:\n{json.dumps(manifest, indent=2)}"
            ),
        }
    ]

    bom_path = product_dir / "diagrams" / "bom.png"
    if bom_path.is_file():
        content.append({"type": "text", "text": "BOM overview diagram:"})
        content.append(_image_part(bom_path))

    for step in manifest.get("steps", []):
        diagram_rel = step.get("diagram", "")
        diagram_path = product_dir / diagram_rel
        if not diagram_path.is_file():
            continue
        content.append(
            {
                "type": "text",
                "text": f"Step {step.get('number')} diagram:",
            }
        )
        content.append(_image_part(diagram_path))

    image_count = sum(1 for part in content if part.get("type") == "image_url")
    if image_count > 20:
        raise EnrichmentError(
            f"Too many diagrams ({image_count}); Cohere allows max 20 images per request."
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=180.0) as client:
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
            raise EnrichmentError(
                f"Cohere API error {response.status_code}: {response.text[:500]}"
            )
        body = response.json()

    raw = _extract_text(body)
    enrichment = _parse_json_payload(raw)
    return _apply_enrichment(manifest, enrichment)


def _image_part(path: Path) -> dict:
    """PNG page render → Cohere image_url content block (base64 data URL)."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            # high detail helps OCR of small 6-digit IKEA part numbers
            "url": f"data:image/png;base64,{b64}",
            "detail": "high",
        },
    }


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
    # Older / alternate shapes
    if "text" in body:
        return str(body["text"])
    raise EnrichmentError(f"Unexpected Cohere response shape: {list(body.keys())}")


def _parse_json_payload(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise EnrichmentError(f"Cohere did not return JSON: {raw[:300]}")
        return json.loads(match.group(0))


def _apply_enrichment(manifest: dict, enrichment: dict) -> dict:
    name_by_part = {
        item["partId"]: item.get("name", "")
        for item in enrichment.get("bom", [])
        if item.get("partId")
    }
    qty_by_part = {
        item["partId"]: item.get("qty")
        for item in enrichment.get("bom", [])
        if item.get("partId") and item.get("qty") is not None
    }
    step_by_number = {
        int(s["number"]): s for s in enrichment.get("steps", []) if "number" in s
    }

    # Prefer enriched BOM when scaffold BOM is thin (common for outline-only web PDFs).
    if enrichment.get("bom") and (
        not manifest.get("bom")
        or sum(1 for b in manifest.get("bom", []) if b.get("name")) == 0
    ):
        bom = [
            {
                "partId": str(item["partId"]),
                "name": item.get("name") or "",
                "qty": int(item.get("qty") or 0),
            }
            for item in enrichment["bom"]
            if item.get("partId")
        ]
    else:
        bom = []
        for item in manifest.get("bom", []):
            bom.append(
                {
                    **item,
                    "name": name_by_part.get(item["partId"]) or item.get("name") or "",
                    "qty": qty_by_part.get(item["partId"], item.get("qty", 0)),
                }
            )

    steps = []
    for step in manifest.get("steps", []):
        extra = step_by_number.get(int(step["number"]), {})
        hw_names = {
            h["partId"]: h.get("name", "")
            for h in extra.get("requiredHardware", [])
            if h.get("partId")
        }
        scaffold_hw = step.get("requiredHardware") or []
        enriched_hw = extra.get("requiredHardware") or []
        source_hw = enriched_hw if (not scaffold_hw and enriched_hw) else scaffold_hw

        required = []
        for hw in source_hw:
            part_id = hw["partId"]
            required.append(
                {
                    "partId": part_id,
                    "name": hw_names.get(part_id)
                    or name_by_part.get(part_id)
                    or hw.get("name")
                    or "",
                    "qty": int(hw.get("qty") or 0),
                }
            )
        steps.append(
            {
                **step,
                "title": extra.get("title")
                or step.get("title")
                or f"Step {step['number']}",
                "requiredHardware": required,
                "completionHints": extra.get("completionHints")
                or step.get("completionHints")
                or [],
            }
        )

    merged = {**manifest, "bom": bom, "steps": steps}
    ProductManifest.model_validate(merged)
    return merged
