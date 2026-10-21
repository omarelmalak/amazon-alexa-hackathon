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
    PartCount,
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
        bom: list[HardwarePart] | None = None,
    ) -> PartIdentityResult:
        frame_png = ensure_png_bytes(frame_bytes)
        candidates = bom or step.requiredHardware
        catalog_txt = ", ".join(
            f"{h.partId} ({h.name or 'part'})" for h in candidates
        ) or "unknown"
        step_ids = {h.partId for h in step.requiredHardware}
        prompt = (
            "Identify the single hardware fastener most prominent in this photo.\n"
            f"Product: {product_id}. Current step {step.number}: {step.title}.\n"
            f"Known parts: {catalog_txt}.\n"
            "Return ONLY JSON:\n"
            '{"partId": string|null, "name": string|null, '
            '"matches_current_step": boolean, "confidence": number, "notes": string|null}'
        )
        data = chat_json(
            content=[
                {"type": "text", "text": prompt},
                png_data_url(frame_png, detail="high"),
            ],
            api_key=self._api_key,
            model=self._model,
        )
        part_id = data.get("partId") or data.get("part_id")
        part_id = str(part_id) if part_id not in (None, "", "null") else None
        matches = data.get("matches_current_step")
        if matches is None:
            matches = data.get("matchesCurrentStep")
        if matches is None and part_id:
            matches = part_id in step_ids
        conf = data.get("confidence")
        try:
            confidence = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        return PartIdentityResult(
            partId=part_id,
            name=(None if not data.get("name") else str(data.get("name"))),
            matchesCurrentStep=bool(matches) if matches is not None else None,
            confidence=confidence,
            notes=(None if not data.get("notes") else str(data.get("notes"))),
        )

    def count_parts(
        self,
        *,
        frame_bytes: bytes,
        bom: list[HardwarePart],
        product_id: str,
    ) -> InventoryCountResult:
        """
        Blind visual count (no expectedQty in the prompt), then compare to BOM in code.

        Asking the model to ENUMERATE instances (not guess a total) reduces the
        common failure mode of echoing the BOM quantity (e.g. always reporting 6).
        """
        frame_png = ensure_png_bytes(frame_bytes)
        # Deliberately omit expected quantities — they bias the vision model.
        catalog_txt = "\n".join(
            f"- partId={h.partId} | look_for={h.name or 'hardware'}" for h in bom
        )
        prompt = (
            "You are counting physical hardware on a table photo for an IKEA kit.\n"
            f"Product: {product_id}\n"
            f"Part types to find (NO target quantities — do not invent a total):\n"
            f"{catalog_txt}\n\n"
            "Visual guide:\n"
            "- hex socket screw: LONG silver/metal threaded fasteners with a shaft. "
            "Count each screw body separately.\n"
            "- allen key: ONE L-shaped hex wrench tool (never count this as a screw).\n"
            "- plastic plug / cap: SMALL round white/beige caps or pins "
            "(much shorter than screws; never count as screws).\n"
            "Ignore wooden seat and legs.\n\n"
            "Method (important):\n"
            "1) For each partId, list EVERY distinct instance you can see as "
            "instances[{where: short location}].\n"
            "2) Do not skip this list. seenQty will be derived from how many "
            "instances you list — so if you see five screws, instances MUST "
            "have length 5, not 6.\n"
            "3) If the image is an official IKEA line-art BOM page with printed "
            "Nx labels, put one instance per labeled unit and mention that in notes.\n"
            "4) Under-count when unsure. Never pad the list to a round number.\n\n"
            "Return ONLY JSON:\n"
            '{"counts":[{"partId":str,"name":str,'
            '"instances":[{"where":str}]}],"notes":str|null}'
        )
        data = chat_json(
            content=[
                {"type": "text", "text": prompt},
                {"type": "text", "text": "Table photo — enumerate each fastener:"},
                png_data_url(frame_png, detail="high"),
            ],
            api_key=self._api_key,
            model=self._model,
        )

        # Optional second pass for screw-like parts if the model still returned
        # a bare seenQty without instances (older / sloppy responses).
        seen_map: dict[str, tuple[str, int, str | None]] = {}
        for row in data.get("counts") or []:
            pid = str(row.get("partId") or row.get("part_id") or "").strip()
            if not pid:
                continue
            name = str(row.get("name") or "")
            instances = row.get("instances")
            evidence = None
            if isinstance(instances, list) and instances:
                seen = len(instances)
                evidence = "; ".join(
                    str(i.get("where") if isinstance(i, dict) else i)
                    for i in instances[:12]
                )
            else:
                try:
                    seen = int(row.get("seenQty") or row.get("seen_qty") or 0)
                except (TypeError, ValueError):
                    seen = 0
                evidence = row.get("evidence") or row.get("tally")
                if evidence is not None:
                    evidence = str(evidence)
            seen_map[pid] = (name, max(0, seen), evidence)

        # Dedicated screw recount: long fasteners are the usual miss (5 vs 6).
        # A focused second call that only enumerates screws is cheaper than YOLO.
        for h in bom:
            if "screw" not in (h.name or "").lower() and h.partId != "130915":
                continue
            if "plug" in (h.name or "").lower() or "allen" in (h.name or "").lower():
                continue
            prior = seen_map.get(h.partId, (h.name, 0, None))
            recount = self._recount_screws(
                frame_png=frame_png,
                part_id=h.partId,
                part_name=prior[0] or h.name or "hex socket screw",
            )
            if recount is not None:
                seen_map[h.partId] = recount

        counted: list[PartCount] = []
        missing: list[PartCount] = []
        for h in bom:
            name, seen, evidence = seen_map.get(h.partId, (h.name, 0, None))
            entry = PartCount(
                partId=h.partId,
                name=name or h.name or "",
                expectedQty=h.qty,
                seenQty=seen,
                evidence=evidence,
            )
            counted.append(entry)
            if h.qty - seen > 0:
                missing.append(entry.model_copy())

        extras: list[PartCount] = []
        known = {h.partId for h in bom}
        for pid, (name, seen, evidence) in seen_map.items():
            if pid not in known and seen > 0:
                extras.append(
                    PartCount(
                        partId=pid,
                        name=name,
                        expectedQty=0,
                        seenQty=seen,
                        evidence=evidence,
                    )
                )

        notes = data.get("notes")
        return InventoryCountResult(
            counted=counted,
            missing=missing,
            extras=extras,
            notes=None if not notes else str(notes),
        )

    def _recount_screws(
        self,
        *,
        frame_png: bytes,
        part_id: str,
        part_name: str,
    ) -> tuple[str, int, str | None] | None:
        """Second vision call: enumerate long metal screws only."""
        prompt = (
            "Count ONLY the long metal screws / bolts in this photo "
            "(threaded shafts). Do NOT count allen keys, plastic caps, "
            "wooden legs, or the seat.\n"
            "List each screw as a separate instances[].where string "
            "(e.g. 'top of column', '2nd from top').\n"
            "If there are five screws, return exactly five instances — "
            "never round up.\n"
            "Return ONLY JSON: "
            '{"partId":"'
            + part_id
            + '","name":"'
            + part_name
            + '","instances":[{"where":str}]}'
        )
        try:
            data = chat_json(
                content=[
                    {"type": "text", "text": prompt},
                    png_data_url(frame_png, detail="high"),
                ],
                api_key=self._api_key,
                model=self._model,
            )
        except CohereError:
            return None
        instances = data.get("instances")
        if not isinstance(instances, list):
            return None
        evidence = "; ".join(
            str(i.get("where") if isinstance(i, dict) else i) for i in instances[:12]
        )
        return (
            str(data.get("name") or part_name),
            len(instances),
            evidence or None,
        )
