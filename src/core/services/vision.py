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
    PartComparison,
    PartCount,
    PartDebugStep,
    PartDetection,
    PartIdentityResult,
)
from infra.cohere_client import CohereError, chat_json, png_data_url
from infra.grounding_dino_parts import (
    DEFAULT_DETECT_PROMPT,
    FastenerDetection,
    crop_detection,
    detect_fasteners,
    detector_name,
)
from infra.parts_library import annotate_detections, bytes_to_data_url


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

        # Pure Cohere A↔B compare — geometry only (tightness is not visible).
        hint_txt = "; ".join(hints) if hints else "n/a"
        prompt = (
            "Compare two images for IKEA flatpack geometry (not torque).\n"
            "  Image A = webcam of the user's build\n"
            "  Image B = manual diagram for THIS step only "
            f"(step {step.number})\n\n"
            f"Product: {product_id}\n"
            f"Step title: {step.title or '(untitled)'}\n"
            f"Required hardware: {', '.join(hardware) or 'n/a'}\n"
            f"Soft hints (geometry only — ignore any 'tighten' language): "
            f"{hint_txt}\n\n"
            "Judge ONLY visible pose and placement vs B:\n"
            "- aligned=true if major parts match B's roles/poses (or further "
            "along the same join), mating surfaces meet as B shows, seat face "
            "matches B, and fasteners are in the holes B shows.\n"
            "- aligned=false if a leg/rail is just resting on the seat, wrong "
            "holes, wrong seat face, wrong step, or parts nearby but not joined.\n"
            "- step_completed=true if aligned AND parts look seated in place "
            "as B's done state (legs attached, screws/plugs in their holes).\n"
            "HARD RULE: You cannot see how tight a screw is from a photo. "
            "NEVER set aligned=false or step_completed=false because screws "
            "'look loose', 'need tightening', 'aren't fully tightened', or "
            "hands are still in frame. Hands / ongoing work ≠ misaligned.\n"
            "error_description: geometry mismatch only, or null. "
            "Never mention tightness/torque.\n"
            "diagram_observed / webcam_observed: 1-2 literal sentences each.\n\n"
            "Return ONLY JSON:\n"
            '{"diagram_observed":str,"webcam_observed":str,'
            '"aligned":bool,"error_description":str|null,'
            '"confidence":number,"step_completed":bool,"notes":str|null}'
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

        aligned = bool(data.get("aligned"))
        err = data.get("error_description") or data.get("errorDescription")
        if aligned:
            err = None if err in ("", None) else str(err)

        conf = data.get("confidence")
        try:
            confidence = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            confidence = None

        diagram_obs = data.get("diagram_observed") or data.get("diagramObserved")
        webcam_obs = data.get("webcam_observed") or data.get("webcamObserved")
        notes = data.get("notes")
        step_done = bool(
            data.get("step_completed")
            if "step_completed" in data
            else data.get("stepCompleted", False)
        )
        # Strip tightness hallucinations from text fields.
        err = _strip_tightness_talk(err)
        notes = _strip_tightness_talk(notes)
        if aligned and not step_done and _is_tightness_only_excuse(notes, err):
            step_done = True
            notes = (
                "Geometry matches the diagram; screw tightness cannot be "
                "judged from a photo."
            )

        return OrientationResult(
            aligned=aligned,
            errorDescription=None if err is None else str(err),
            boundingBox=None,
            confidence=confidence,
            stepCompleted=step_done,
            rawNotes=None if notes in (None, "") else str(notes),
            diagramStep=step.number,
            diagramTitle=step.title,
            diagramBytes=len(diagram_png),
            diagramPreviewBase64=bytes_to_data_url(
                _jpeg_preview(diagram_png, max_side=480, quality=60),
                mime="image/jpeg",
            ),
            annotatedFrameBase64=None,
            diagramObserved=None if diagram_obs in (None, "") else str(diagram_obs),
            webcamObserved=None if webcam_obs in (None, "") else str(webcam_obs),
        )

    def identify_part(
        self,
        *,
        frame_bytes: bytes,
        step: Step,
        product_id: str,
        bom: list[HardwarePart] | None = None,
        reference_images: dict[str, bytes] | None = None,
        detect_prompt: str | None = None,
    ) -> PartIdentityResult:
        """
        GroundingDINO-isolate each screw (open-vocab prompt), compare every crop
        to the step's correct part photo, pick the highest similarity.
        """
        frame_png = ensure_png_bytes(frame_bytes)
        prompt = (detect_prompt or DEFAULT_DETECT_PROMPT).strip()
        name_by_id = {
            h.partId: (h.name or "")
            for h in (bom or []) + list(step.requiredHardware)
        }
        step_ids = {h.partId for h in step.requiredHardware}
        target = _target_screw_part(step, bom)
        target_id = target.partId if target else None

        all_refs = dict(reference_images or {})
        # Score against the CORRECT part + other BOM distractors (forces separation).
        refs: dict[str, bytes] = {}
        if target_id and target_id in all_refs:
            refs[target_id] = all_refs[target_id]
        for h in (bom or []) + list(step.requiredHardware):
            if h.partId in all_refs and h.partId not in refs:
                # Prefer other screw-like distractors over allen keys.
                name = (h.name or "").lower()
                if "allen" in name or "key" in name:
                    continue
                refs[h.partId] = all_refs[h.partId]
            if len(refs) >= 3:
                break
        if not refs:
            refs = {
                k: v
                for k, v in all_refs.items()
                if k in step_ids or not step_ids
            }

        debug_steps: list[PartDebugStep] = [
            PartDebugStep(
                kind="status",
                title="GroundingDINO",
                text=f"Prompt: {prompt!r} · target part={target_id or 'n/a'}",
            )
        ]

        detections_raw = detect_fasteners(frame_png, prompt=prompt)
        det_name = detector_name()
        if self._needs_box_fallback(detections_raw, frame_png):
            proposed = self._propose_fastener_boxes(frame_png)
            if len(proposed) >= 2 or (
                proposed and detections_raw and detections_raw[0].label == "frame"
            ):
                detections_raw = proposed
                det_name = f"{det_name}+vision-boxes"

        detections: list[PartDetection] = [
            PartDetection(
                index=d.index,
                label=d.label,
                score=d.score,
                box=BoundingBox(x=d.x, y=d.y, w=d.w, h=d.h),
            )
            for d in detections_raw
        ]
        debug_steps.append(
            PartDebugStep(
                kind="status",
                title=f"Detections ({det_name})",
                text=(
                    f"Found {len(detections)} box(es): "
                    + ", ".join(
                        f"#{d.index + 1} {d.label}@{d.score or 0:.2f}"
                        for d in detections
                    )
                ),
            )
        )

        comparisons: list[PartComparison] = []
        crops_jpeg: dict[int, str] = {}
        if refs:
            ref_previews = {
                pid: bytes_to_data_url(_jpeg_preview(ensure_png_bytes(b)), mime="image/jpeg")
                for pid, b in refs.items()
            }
            for det in detections_raw:
                crop = crop_detection(frame_png, det)
                crop_url = bytes_to_data_url(
                    _jpeg_preview(ensure_png_bytes(crop)), mime="image/jpeg"
                )
                crops_jpeg[det.index] = crop_url
                debug_steps.append(
                    PartDebugStep(
                        kind="crop",
                        title=f"Crop #{det.index + 1}",
                        text=(
                            f"GroundingDINO «{det.label}» "
                            f"box=({det.x},{det.y},{det.w}×{det.h}) "
                            f"score={det.score:.2f}"
                        ),
                        detectionIndex=det.index,
                        imageBase64=crop_url,
                    )
                )
                for part_id, ref_bytes in refs.items():
                    sim, note = self._compare_crop_to_reference(
                        crop_png=ensure_png_bytes(crop),
                        ref_png=ensure_png_bytes(ref_bytes),
                        part_id=part_id,
                        part_name=name_by_id.get(part_id) or "",
                    )
                    comparisons.append(
                        PartComparison(
                            detectionIndex=det.index,
                            partId=part_id,
                            partName=name_by_id.get(part_id) or None,
                            similarity=sim,
                            notes=note,
                        )
                    )
                    debug_steps.append(
                        PartDebugStep(
                            kind="compare",
                            title=f"Compare #{det.index + 1} ↔ {part_id}",
                            text=(
                                f"{sim:.0%} vs correct part "
                                f"{part_id} ({name_by_id.get(part_id) or 'part'})"
                                + (f" — {note}" if note else "")
                            ),
                            detectionIndex=det.index,
                            partId=part_id,
                            similarity=sim,
                            imageBase64=crop_url,
                            refImageBase64=ref_previews.get(part_id),
                        )
                    )
        else:
            return self._identify_part_without_refs(
                frame_png=frame_png,
                step=step,
                product_id=product_id,
                bom=bom,
                detections=detections,
                detector=det_name,
                detect_prompt=prompt,
                debug_steps=debug_steps,
            )

        # Winner = crop that best matches the CORRECT (target) part.
        target_comps = [
            c for c in comparisons if target_id is None or c.partId == target_id
        ] or comparisons
        best: PartComparison | None = None
        for row in target_comps:
            if best is None or row.similarity > best.similarity:
                best = row

        winner_idx = best.detectionIndex if best else None
        ann_payload = []
        for d in detections:
            det_best = max(
                (c for c in comparisons if c.detectionIndex == d.index),
                key=lambda c: c.similarity,
                default=None,
            )
            label = (
                f"#{d.index + 1} {det_best.partId}"
                if det_best
                else f"#{d.index + 1}"
            )
            ann_payload.append(
                {
                    "box": d.box.model_dump(),
                    "label": label,
                    "score": det_best.similarity if det_best else d.score,
                }
            )
        annotated = annotate_detections(
            frame_png, ann_payload, winner_index=winner_idx
        )
        ann_url = bytes_to_data_url(_jpeg_preview(annotated), mime="image/jpeg")
        debug_steps.insert(
            1,
            PartDebugStep(
                kind="annotated",
                title="Boxes on webcam",
                text=f"{len(detections)} GroundingDINO box(es); winner highlighted green",
                imageBase64=ann_url,
            ),
        )

        part_id = best.partId if best else None
        conf = best.similarity if best else None
        matches = (part_id in step_ids) if part_id else False
        spoken = self._spoken_match_notes(
            best=best,
            comparisons=comparisons,
            step=step,
            matches=bool(matches),
        )
        ranked = sorted(comparisons, key=lambda c: c.similarity, reverse=True)
        debug_steps.append(
            PartDebugStep(
                kind="winner",
                title="Winner",
                text=spoken,
                detectionIndex=winner_idx,
                partId=part_id,
                similarity=conf,
                imageBase64=crops_jpeg.get(winner_idx) if winner_idx is not None else None,
            )
        )
        return PartIdentityResult(
            partId=part_id,
            name=(name_by_id.get(part_id) if part_id else None) or None,
            matchesCurrentStep=matches,
            confidence=conf,
            notes=spoken,
            detector=det_name,
            detectPrompt=prompt,
            targetPartId=target_id,
            detections=detections,
            comparisons=ranked,
            winnerDetectionIndex=winner_idx,
            annotatedFrameBase64=ann_url,
            referencePartIds=sorted(refs.keys()),
            debugPipeline=_pipeline_summary(
                detector=det_name,
                detections=detections,
                comparisons=ranked,
                winner_idx=winner_idx,
                part_id=part_id,
                refs=sorted(refs.keys()),
            ),
            debugSteps=debug_steps,
        )

    def _needs_box_fallback(
        self, detections: list[FastenerDetection], frame_png: bytes
    ) -> bool:
        if not detections:
            return True
        if len(detections) == 1 and detections[0].label == "frame":
            return True
        # Single tiny/large blob when the user is clearly holding multiple fasteners.
        if len(detections) == 1:
            image = Image.open(io.BytesIO(frame_png))
            iw, ih = image.size
            area = detections[0].w * detections[0].h
            if area > 0.55 * iw * ih or area < 0.01 * iw * ih:
                return True
        return False

    def _propose_fastener_boxes(self, frame_png: bytes) -> list[FastenerDetection]:
        """Vision fallback: one bounding box per distinct fastener in the frame."""
        prompt = (
            "Locate EVERY distinct screw, bolt, allen key, or plastic plug "
            "the user is holding or placing in this photo. "
            "If they hold TWO different screws, return TWO boxes.\n"
            "Return ONLY JSON: "
            '{"boxes":[{"label":str,"x":int,"y":int,"w":int,"h":int}]}\n'
            "Pixel coords, origin top-left. Do not merge two fasteners into one box."
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
            return []
        image = Image.open(io.BytesIO(frame_png))
        iw, ih = image.size
        out: list[FastenerDetection] = []
        for row in data.get("boxes") or []:
            try:
                x = max(0, int(row.get("x", 0)))
                y = max(0, int(row.get("y", 0)))
                bw = max(1, int(row.get("w", row.get("width", 0))))
                bh = max(1, int(row.get("h", row.get("height", 0))))
            except (TypeError, ValueError):
                continue
            if bw < 8 or bh < 8:
                continue
            if x >= iw or y >= ih:
                continue
            bw = min(bw, iw - x)
            bh = min(bh, ih - y)
            out.append(
                FastenerDetection(
                    index=len(out),
                    label=str(row.get("label") or "fastener"),
                    score=0.5,
                    x=x,
                    y=y,
                    w=bw,
                    h=bh,
                )
            )
        return out

    def _compare_crop_to_reference(
        self,
        *,
        crop_png: bytes,
        ref_png: bytes,
        part_id: str,
        part_name: str,
    ) -> tuple[float, str | None]:
        prompt = (
            "You are a STRICT IKEA hardware matcher. Do NOT give mid/high scores "
            "just because both images show 'a screw'.\n"
            "Image A = webcam crop of ONE fastener.\n"
            "Image B = reference for part "
            f"{part_id} ({part_name or 'hardware'}).\n\n"
            "Score these features independently 0 or 1:\n"
            "- drive: hex socket vs phillips vs slotted vs none (plastic plug)\n"
            "- head: button/pan vs flat vs dome vs L-tool\n"
            "- shaft: long fully-threaded metal vs short barbed plastic plug vs hex key arm\n"
            "- proportion: length/diameter similar to B (±30%)\n\n"
            "HARD RULES:\n"
            "- If drive OR shaft family mismatches → same_part=false and "
            "similarity MUST be ≤ 0.30\n"
            "- 'Both are screws' is NOT enough for a high score\n"
            "- similarity ≥ 0.80 only if ALL four features match\n"
            "- similarity 0.55–0.75 only if 3/4 match\n"
            "- If A shows TWO fasteners or is ambiguous → similarity ≤ 0.25\n\n"
            "Return ONLY JSON:\n"
            '{"features":{"drive":0|1,"head":0|1,"shaft":0|1,"proportion":0|1},'
            '"same_part":boolean,"similarity":number,"notes":string}'
        )
        try:
            data = chat_json(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": "Image A — webcam crop:"},
                    png_data_url(crop_png, detail="high"),
                    {"type": "text", "text": "Image B — reference part:"},
                    png_data_url(ref_png, detail="high"),
                ],
                api_key=self._api_key,
                model=self._model,
            )
        except CohereError:
            return 0.0, "comparison failed"

        feats = data.get("features") if isinstance(data.get("features"), dict) else {}
        keys = ("drive", "head", "shaft", "proportion")
        scores: list[float] = []
        for k in keys:
            try:
                scores.append(1.0 if float(feats.get(k, 0)) >= 0.5 else 0.0)
            except (TypeError, ValueError):
                scores.append(0.0)
        feature_avg = sum(scores) / len(scores) if scores else 0.0

        sim = data.get("similarity")
        try:
            similarity = float(sim) if sim is not None else feature_avg
        except (TypeError, ValueError):
            similarity = feature_avg

        # Prefer feature checklist over the model's often-inflated similarity.
        similarity = min(similarity, feature_avg if feature_avg > 0 else similarity)
        if scores and scores[0] < 0.5:  # drive mismatch
            similarity = min(similarity, 0.30)
        if scores and scores[2] < 0.5:  # shaft family mismatch
            similarity = min(similarity, 0.30)
        if data.get("same_part") is False:
            similarity = min(similarity, 0.35)
        if feature_avg >= 0.999:
            similarity = max(similarity, 0.82)
        elif feature_avg >= 0.75:
            similarity = min(max(similarity, 0.55), 0.75)
        elif feature_avg <= 0.5:
            similarity = min(similarity, 0.40)

        similarity = max(0.0, min(1.0, similarity))
        notes = data.get("notes")
        feat_txt = ",".join(f"{k}={int(s)}" for k, s in zip(keys, scores))
        if notes:
            notes = f"[{feat_txt}] {notes}"
        else:
            notes = f"[{feat_txt}]"
        return similarity, notes

    def _spoken_match_notes(
        self,
        *,
        best: PartComparison | None,
        comparisons: list[PartComparison],  # noqa: ARG002 — kept for call sites
        step: Step,
        matches: bool,
    ) -> str:
        """Plain-language note for the agent — no % scores or detection indices."""
        _ = comparisons
        if best is None:
            return (
                "Could not match what is in the frame to the kit part photos."
            )
        name = (best.partName or "").strip() or "that fastener"
        look = _part_look_phrase(name)
        if matches:
            return (
                f"This is the {look} — the one you need for step {step.number}."
            )
        needed = [
            _part_look_phrase(h.name or h.partId)
            for h in step.requiredHardware
            if (h.name or h.partId)
        ]
        need_txt = ", ".join(needed[:3]) if needed else "the parts for this step"
        return (
            f"This looks like the {look}, which is not what step {step.number} "
            f"needs. For this step use: {need_txt}."
        )

    def _identify_part_without_refs(
        self,
        *,
        frame_png: bytes,
        step: Step,
        product_id: str,
        bom: list[HardwarePart] | None,
        detections: list[PartDetection],
        detector: str,
        detect_prompt: str | None = None,
        debug_steps: list[PartDebugStep] | None = None,
    ) -> PartIdentityResult:
        candidates = bom or step.requiredHardware
        catalog_txt = ", ".join(
            f"{h.partId} ({h.name or 'part'})" for h in candidates
        ) or "unknown"
        step_ids = {h.partId for h in step.requiredHardware}
        needed = ", ".join(
            f"{h.partId} ({h.name or 'part'}) x{h.qty}" for h in step.requiredHardware
        ) or "n/a"
        prompt = (
            "You are helping someone pick the correct IKEA hardware from a live photo.\n"
            f"Product: {product_id}. Current step {step.number}: {step.title}.\n"
            f"Parts needed for THIS step: {needed}.\n"
            f"Known catalog parts: {catalog_txt}.\n"
            "WARNING: no local reference photos were available — be conservative.\n\n"
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
        annotated = annotate_detections(
            frame_png,
            [
                {
                    "box": d.box.model_dump(),
                    "label": f"#{d.index + 1}",
                    "score": d.score,
                }
                for d in detections
            ],
            winner_index=0 if detections else None,
        )
        note = (
            (None if not data.get("notes") else str(data.get("notes")))
            or "No parts/ reference photos found — add them under data/<product>/parts/."
        )
        steps = list(debug_steps or [])
        ann_url = bytes_to_data_url(_jpeg_preview(annotated), mime="image/jpeg")
        steps.append(
            PartDebugStep(
                kind="annotated",
                title="Boxes (no parts DB)",
                text="Missing data/<product>/parts/{partId}.png — cannot score crops",
                imageBase64=ann_url,
            )
        )
        return PartIdentityResult(
            partId=part_id,
            name=(None if not data.get("name") else str(data.get("name"))),
            matchesCurrentStep=bool(matches) if matches is not None else None,
            confidence=confidence,
            notes=note,
            detector=detector,
            detectPrompt=detect_prompt,
            detections=detections,
            comparisons=[],
            winnerDetectionIndex=0 if detections else None,
            annotatedFrameBase64=ann_url,
            referencePartIds=[],
            debugPipeline=[
                f"1. Detector={detector}; isolated {len(detections)} region(s)",
                "2. No parts/{partId} reference photos — skipped crop compare",
                f"3. Whole-frame guess → {part_id or 'none'} "
                f"(confidence={confidence})",
                "4. Drop real photos into data/<product>/parts/ for proper matching",
            ],
            debugSteps=steps,
        )

    def count_parts(
        self,
        *,
        frame_bytes: bytes,
        bom: list[HardwarePart],
        product_id: str,
        reference_images: dict[str, bytes] | None = None,
        detect_prompt: str | None = None,
    ) -> InventoryCountResult:
        """
        Inventory: Cohere lists each visible piece; we count the list.

        Never trust a model-supplied integer total (it invents kit sizes).
        seenQty = number of accepted piece rows for that partId.
        """
        del detect_prompt, reference_images, product_id  # no qty / kit priming
        frame_png = ensure_png_bytes(frame_bytes)

        catalog = "\n".join(
            f"- partId={h.partId} means: {h.name or 'hardware'}" for h in bom
        )
        allowed = {h.partId: h for h in bom}

        prompt = (
            "Look at this ONE webcam photo of (maybe) loose IKEA hardware.\n\n"
            f"Allowed partId labels:\n{catalog}\n\n"
            "Rules:\n"
            "1) scene_ok=true only if you clearly see loose hardware on a "
            "table/floor. Faces, empty desks, phone UI → scene_ok=false and "
            "pieces=[].\n"
            "2) pieces = one JSON object PER physical piece you can see. "
            "Five screws → five objects. Do NOT output totals or kit sizes.\n"
            "3) Each piece needs partId (from the list) and where = a short "
            "location (must include a word like left/right/top/bottom/center/"
            "edge/corner/near/above/below). Example: "
            '"long silver screw at top-left".\n'
            "4) Forbidden where values: "
            '"six screws", "three plugs", "all the hardware", or any phrase '
            "that summarizes multiple pieces in one row.\n"
            "5) If unsure a blob is a part, omit it (under-count).\n\n"
            "Return ONLY JSON:\n"
            '{"scene_ok":bool,"scene":str,'
            '"pieces":[{"partId":str,"where":str}],"notes":str|null}'
        )

        debug_steps: list[PartDebugStep] = [
            PartDebugStep(
                kind="status",
                title="Inventory (piece list)",
                text=f"Types: {', '.join(f'{h.partId}={h.name}' for h in bom)}",
            )
        ]

        data = chat_json(
            content=[
                {"type": "text", "text": prompt},
                {"type": "text", "text": "Webcam photo:"},
                png_data_url(frame_png, detail="high"),
            ],
            api_key=self._api_key,
            model=self._model,
            temperature=0.0,
        )

        scene_ok = bool(
            data.get("scene_ok") if "scene_ok" in data else data.get("sceneOk")
        )
        scene = data.get("scene")
        debug_steps.append(
            PartDebugStep(
                kind="status",
                title="Scene gate",
                text=f"scene_ok={scene_ok}" + (f" — {scene}" if scene else ""),
            )
        )

        buckets: dict[str, list[str]] = {h.partId: [] for h in bom}
        rejected_rows = 0
        if scene_ok:
            raw_pieces = data.get("pieces") or data.get("counts") or []
            for row in raw_pieces:
                if not isinstance(row, dict):
                    rejected_rows += 1
                    continue
                pid = str(row.get("partId") or row.get("part_id") or "").strip()
                if pid not in allowed:
                    rejected_rows += 1
                    continue
                where = str(
                    row.get("where")
                    or row.get("evidence")
                    or row.get("note")
                    or ""
                ).strip()
                # Legacy shape: counts[].evidence as a list of wheres
                if not where and isinstance(row.get("evidence"), list):
                    for item in row["evidence"]:
                        w = str(item).strip()
                        if _accept_piece_where(w):
                            buckets[pid].append(w)
                        else:
                            rejected_rows += 1
                    continue
                if not _accept_piece_where(where):
                    rejected_rows += 1
                    continue
                # Skip near-duplicate locations for the same part
                key = re.sub(r"\s+", " ", where.lower())
                if key in {re.sub(r"\s+", " ", x.lower()) for x in buckets[pid]}:
                    rejected_rows += 1
                    continue
                buckets[pid].append(where)

        counted: list[PartCount] = []
        missing: list[PartCount] = []
        for h in bom:
            wheres = buckets[h.partId][: max(0, h.qty)]
            seen = len(wheres)
            entry = PartCount(
                partId=h.partId,
                name=h.name or "",
                expectedQty=h.qty,
                seenQty=seen,
                evidence="; ".join(wheres) if wheres else None,
            )
            counted.append(entry)
            if h.qty - seen > 0:
                missing.append(entry.model_copy())

        summary = ", ".join(
            f"{c.name or c.partId} {c.seenQty}/{c.expectedQty}" for c in counted
        )
        debug_steps.append(
            PartDebugStep(
                kind="status",
                title="Tally (len(pieces) only)",
                text=summary
                + (f" · dropped {rejected_rows} bad row(s)" if rejected_rows else ""),
            )
        )
        notes = data.get("notes")
        if not scene_ok:
            notes = (
                f"Not a parts layout ({scene or 'scene rejected'}). "
                "All counts set to 0."
            )
        return InventoryCountResult(
            counted=counted,
            missing=missing,
            extras=[],
            notes=(None if not notes else str(notes)) or f"Count: {summary}",
            detector="cohere-pieces",
            detectPrompt=None,
            annotatedFrameBase64=None,
            debugSteps=debug_steps,
        )



_TIGHTNESS_TALK = re.compile(
    r"\b("
    r"tighten|tightened|tightening|torque|fully\s+tight|"
    r"not\s+fully\s+tight|loose\s+screw|screws?\s+have\s+not|"
    r"haven'?t\s+been\s+fully\s+tight"
    r")\b",
    re.I,
)


def _strip_tightness_talk(text: str | None) -> str | None:
    """Drop unverifiable screw-tightness claims from model notes."""
    if text is None or text == "":
        return None if text is None else text
    if not _TIGHTNESS_TALK.search(text):
        return str(text)
    # Prefer nulling error_description-style short claims; keep notes cleaned.
    cleaned = _TIGHTNESS_TALK.sub("", str(text))
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,;")
    return cleaned or None


def _is_tightness_only_excuse(
    notes: str | None, err: str | None
) -> bool:
    """True when the only reason step looks incomplete is invented tightness."""
    blob = f"{notes or ''} {err or ''}".strip().lower()
    if not blob:
        return False
    if not _TIGHTNESS_TALK.search(blob):
        return False
    # Geometry failure keywords → not tightness-only.
    geometry = (
        "wrong",
        "resting",
        "lying",
        "upside",
        "mirrored",
        "missing leg",
        "not in",
        "misaligned",
        "face down",
        "face up",
    )
    return not any(g in blob for g in geometry)


_LOC_WORD = re.compile(
    r"\b("
    r"left|right|top|bottom|center|centre|middle|near|above|below|"
    r"edge|corner|beside|next|upper|lower|row|column|side|front|back|"
    r"northwest|northeast|southwest|southeast"
    r")\b",
    re.I,
)
_BATCH_SUMMARY = re.compile(
    r"^\s*("
    r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"\d{1,2}"
    r")\b",
    re.I,
)


def _accept_piece_where(where: str) -> bool:
    """
    Keep only per-piece locations. Drop batch summaries like
    'six long silver fasteners' that inflate counts without listing items.
    """
    w = (where or "").strip()
    if len(w) < 4 or len(w) > 160:
        return False
    if _BATCH_SUMMARY.search(w):
        return False
    if not _LOC_WORD.search(w):
        return False
    return True


def _part_look_phrase(name: str) -> str:
    """Turn a BOM name into a short look-based phrase for spoken notes."""
    n = (name or "").strip().lower()
    if not n:
        return "fastener"
    if "allen" in n or ("key" in n and "hex" in n):
        return "L-shaped allen key"
    if "plug" in n:
        return "short plastic push plug"
    if "screw" in n or "bolt" in n:
        if "hex" in n or "socket" in n:
            return "long silver hex screw"
        return f"{n}"
    return n


def _target_screw_part(
    step: Step, bom: list[HardwarePart] | None
) -> HardwarePart | None:
    """Prefer a screw/bolt over allen keys for the step's 'correct' reference."""
    pool = list(step.requiredHardware) or list(bom or [])
    screws = [
        h
        for h in pool
        if any(
            k in (h.name or "").lower()
            for k in ("screw", "bolt", "plug", "rivet", "fastener")
        )
        and "allen" not in (h.name or "").lower()
        and "key" not in (h.name or "").lower()
    ]
    if screws:
        # Prefer positive qty when listed.
        screws.sort(key=lambda h: (0 if h.qty > 0 else 1, h.partId))
        return screws[0]
    return pool[0] if pool else None


def _jpeg_preview(png_bytes: bytes, *, max_side: int = 640, quality: int = 55) -> bytes:
    """Small JPEG for SSE/debug UI (boxes stay visible; keeps events parseable)."""
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = image.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def _pipeline_summary(
    *,
    detector: str,
    detections: list[PartDetection],
    comparisons: list[PartComparison],
    winner_idx: int | None,
    part_id: str | None,
    refs: list[str],
) -> list[str]:
    lines = [
        f"1. Isolate fasteners ({detector}): {len(detections)} box(es)",
    ]
    for d in detections:
        lines.append(
            f"   · #{d.index + 1} {d.label} @ ({d.box.x},{d.box.y}) "
            f"{d.box.w}×{d.box.h}"
        )
    lines.append(
        f"2. Compare each crop to parts DB refs: {', '.join(refs) or '(none)'}"
    )
    for c in comparisons[:12]:
        mark = "★" if winner_idx == c.detectionIndex and c.partId == part_id else " "
        lines.append(
            f"   {mark} det#{c.detectionIndex + 1} ↔ {c.partId} = "
            f"{c.similarity:.0%}"
        )
    lines.append(
        f"3. Winner = highest similarity → {part_id or 'none'} "
        f"(detection #{(winner_idx or 0) + 1})"
    )
    return lines
