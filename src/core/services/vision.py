from __future__ import annotations

import base64
import io
import re

from PIL import Image

from core.models.catalog import HardwarePart, Step
from core.models.vision import (
    BoundingBox,
    InventoryCountResult,
    OrientationResult,
    PartIdentityResult,
)
from infra.cohere_client import CohereError, chat_json, png_data_url


class VisionNotImplementedError(NotImplementedError):
    pass


def decode_frame_base64(frame_base64: str) -> bytes:
    """Accept raw base64 or a data URL; return image bytes (PNG/JPEG/…)."""
    raw = frame_base64.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    raw = re.sub(r"\s+", "", raw)
    try:
        return base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid frameBase64") from exc


def ensure_png_bytes(image_bytes: bytes) -> bytes:
    """Normalize webcam frames to PNG for Cohere image_url blocks."""
    if image_bytes.startswith(b"\x89PNG"):
        return image_bytes
    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class CohereVisionInspector:
    """Tier-2 spatial checker: webcam frame vs manual step diagram."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key
        self._model = model

    def verify_orientation(
        self,
        *,
        frame_bytes: bytes,
        diagram_bytes: bytes,
        step: Step,
        product_id: str,
    ) -> OrientationResult:
        frame_png = ensure_png_bytes(frame_bytes)
        diagram_png = ensure_png_bytes(diagram_bytes)
        hints = step.completionHints or []
        hardware = [
            f"{h.partId} ({h.name or 'part'}) x{h.qty}" for h in step.requiredHardware
        ]

        prompt = (
            "You are a strict visual QA checker for IKEA assembly.\n"
            "You will receive TWO images:\n"
            "  Image A = what the user currently has (webcam / candidate)\n"
            "  Image B = the official manual diagram for THIS step only\n\n"
            f"Product: {product_id}\n"
            f"Expected step {step.number}: {step.title or '(untitled)'}\n"
            f"Required hardware: {', '.join(hardware) or 'n/a'}\n"
            f"Completion hints: {'; '.join(hints) or 'n/a'}\n\n"
            "Task: decide whether Image A shows the SAME assembly state as Image B.\n"
            "aligned MUST be false when ANY of these are true:\n"
            "- A is a different assembly step than B\n"
            "- A is rotated, mirrored, or upside-down relative to B\n"
            "- major parts/hardware actions in B are missing or different in A\n"
            "- A is blank, scribbled, damaged, or not a plausible match\n"
            "aligned is true ONLY if A and B depict the same step configuration "
            "with matching orientation.\n"
            "If aligned is false, put a pixel bounding_box on the mismatch in A "
            "(x,y,w,h; origin top-left) and explain in error_description.\n"
            "step_completed is true only if aligned is true AND the step looks done.\n\n"
            "Return ONLY compact JSON (no markdown) with keys:\n"
            "aligned (bool), error_description (string|null),\n"
            "bounding_box ({x,y,w,h}|null), confidence (0..1),\n"
            "step_completed (bool), notes (string|null).\n"
            "Choose aligned true or false based on the images — do not default."
        )

        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": "Image A — webcam / physical assembly:"},
            png_data_url(frame_png, detail="high"),
            {"type": "text", "text": "Image B — manual diagram for this step:"},
            png_data_url(diagram_png, detail="high"),
        ]

        try:
            data = chat_json(
                content=content,
                api_key=self._api_key,
                model=self._model,
            )
        except CohereError as exc:
            raise CohereError(str(exc)) from exc

        box = None
        raw_box = data.get("bounding_box") or data.get("boundingBox")
        if isinstance(raw_box, dict):
            try:
                box = BoundingBox(
                    x=int(raw_box.get("x", 0)),
                    y=int(raw_box.get("y", 0)),
                    w=int(raw_box.get("w", raw_box.get("width", 0))),
                    h=int(raw_box.get("h", raw_box.get("height", 0))),
                )
            except Exception:  # noqa: BLE001
                box = None

        aligned = bool(data.get("aligned"))
        err = data.get("error_description") or data.get("errorDescription")
        if aligned:
            err = None if err in ("", None) else str(err)

        conf = data.get("confidence")
        try:
            confidence = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            confidence = None

        return OrientationResult(
            aligned=aligned,
            errorDescription=None if err is None else str(err),
            boundingBox=box,
            confidence=confidence,
            stepCompleted=bool(
                data.get("step_completed")
                if "step_completed" in data
                else data.get("stepCompleted", False)
            ),
            rawNotes=(
                None
                if data.get("notes") in (None, "")
                else str(data.get("notes"))
            ),
        )

    def identify_part(
        self,
        *,
        frame_bytes: bytes,
        step: Step,
        product_id: str,
    ) -> PartIdentityResult:
        raise VisionNotImplementedError(
            "identify_part not implemented yet — Phase 2 stub"
        )

    def count_parts(
        self,
        *,
        frame_bytes: bytes,
        bom: list[HardwarePart],
        product_id: str,
    ) -> InventoryCountResult:
        raise VisionNotImplementedError(
            "count_parts not implemented yet — Phase 2 stub"
        )
