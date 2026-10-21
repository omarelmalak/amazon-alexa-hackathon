from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx

from core.models.catalog import ProductManifest


class EnrichmentError(RuntimeError):
    pass


def enrich_manifest_with_vision(
    product_dir: Path,
    manifest: dict,
    *,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Fill part names, step titles, and completionHints using a vision LLM.

    Requires OPENAI_API_KEY (or api_key=). No-ops with a clear error if missing.
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise EnrichmentError(
            "OPENAI_API_KEY not set — skipping LLM enrichment. "
            "Set the key or pass enrich=false."
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
                "- Keep all partId and qty values unchanged.\n"
                "- Give short English hardware names (e.g. 'hex socket screw', 'allen key').\n"
                "- Give a concise step title describing the physical action.\n"
                "- completionHints: 2-4 observable checks a camera/inspector could verify "
                "when the step is done (orientation, flush fasteners, part placement).\n"
                "- Respond with ONLY JSON matching this schema:\n"
                '{ "bom": [{"partId": str, "name": str}], '
                '"steps": [{"number": int, "title": str, '
                '"requiredHardware": [{"partId": str, "name": str}], '
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

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    raw = body["choices"][0]["message"]["content"]
    enrichment = json.loads(raw)
    return _apply_enrichment(manifest, enrichment)


def _image_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{b64}"},
    }


def _apply_enrichment(manifest: dict, enrichment: dict) -> dict:
    name_by_part = {
        item["partId"]: item.get("name", "")
        for item in enrichment.get("bom", [])
        if item.get("partId")
    }
    step_by_number = {
        int(s["number"]): s for s in enrichment.get("steps", []) if "number" in s
    }

    bom = []
    for item in manifest.get("bom", []):
        bom.append(
            {
                **item,
                "name": name_by_part.get(item["partId"]) or item.get("name") or "",
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
        required = []
        for hw in step.get("requiredHardware", []):
            required.append(
                {
                    **hw,
                    "name": hw_names.get(hw["partId"])
                    or name_by_part.get(hw["partId"])
                    or hw.get("name")
                    or "",
                }
            )
        steps.append(
            {
                **step,
                "title": extra.get("title") or step.get("title") or f"Step {step['number']}",
                "requiredHardware": required,
                "completionHints": extra.get("completionHints")
                or step.get("completionHints")
                or [],
            }
        )

    merged = {**manifest, "bom": bom, "steps": steps}
    # Validate shape
    ProductManifest.model_validate(merged)
    return merged
