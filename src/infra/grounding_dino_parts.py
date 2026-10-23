"""GroundingDINO open-vocab fastener detection (replaces YOLO-World)."""

from __future__ import annotations

import io
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_DIR = ROOT / "weights"
WEIGHTS_SWINT = WEIGHTS_DIR / "groundingdino_swint_ogc.pth"
WEIGHTS_URL = (
    "https://github.com/IDEA-Research/GroundingDINO/releases/download/"
    "v0.1.0-alpha/groundingdino_swint_ogc.pth"
)

# Singular classes only — plural "screws" makes DINO wrap multiple fasteners in one box.
DEFAULT_DETECT_PROMPT = "hex socket screw . screw . bolt . allen key ."

BOX_THRESHOLD = 0.25
TEXT_THRESHOLD = 0.25
MIN_BOX_WIDTH = 0.02
MIN_BOX_HEIGHT = 0.02
MAX_BOX_WIDTH = 0.55
MAX_BOX_HEIGHT = 0.70
NMS_IOU = 0.35

_model: Any = None
_model_failed = False
_fail_reason: str | None = None


@dataclass
class FastenerDetection:
    index: int
    label: str
    score: float
    x: int
    y: int
    w: int
    h: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "score": round(self.score, 4),
            "box": {"x": self.x, "y": self.y, "w": self.w, "h": self.h},
        }


def detect_fasteners(
    frame_bytes: bytes,
    *,
    prompt: str = DEFAULT_DETECT_PROMPT,
    box_threshold: float = BOX_THRESHOLD,
    text_threshold: float = TEXT_THRESHOLD,
    max_det: int = 8,
) -> list[FastenerDetection]:
    """
    Run GroundingDINO with an open-vocab caption (e.g. \"hex socket screw . screw .\").

    Returns pixel boxes. Falls back to full-frame if the model is unavailable.
    """
    image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    iw, ih = image.size
    model = _get_model()
    if model is None:
        return [_full_frame(iw, ih)]

    caption = _singularize_prompt(prompt or DEFAULT_DETECT_PROMPT)
    if not caption.endswith("."):
        caption = caption + " ."

    try:
        boxes, logits, phrases = _predict_bytes(
            model,
            frame_bytes,
            caption=caption,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GroundingDINO predict failed: %s", exc)
        return [_full_frame(iw, ih)]

    detections: list[FastenerDetection] = []
    kept_boxes: list[Any] = []
    kept_logits: list[Any] = []
    kept_phrases: list[str] = []

    for box, logit, phrase in zip(boxes, logits, phrases):
        try:
            bw = float(box[2].item() if hasattr(box[2], "item") else box[2])
            bh = float(box[3].item() if hasattr(box[3], "item") else box[3])
            score = float(logit.item() if hasattr(logit, "item") else logit)
            label = str(phrase).strip() or "screw"
        except Exception:  # noqa: BLE001
            continue
        if (
            bw < MIN_BOX_WIDTH
            or bh < MIN_BOX_HEIGHT
            or bw > MAX_BOX_WIDTH
            or bh > MAX_BOX_HEIGHT
        ):
            continue
        kept_boxes.append(box)
        kept_logits.append(logit)
        kept_phrases.append(label)

    if not kept_boxes:
        logger.info(
            "GroundingDINO found no boxes for prompt=%r (raw=%d)",
            caption,
            len(boxes) if boxes is not None else 0,
        )
        return [_full_frame(iw, ih)]

    try:
        import torch
        import torchvision

        viz_boxes = torch.stack(kept_boxes)
        viz_logits = torch.stack(
            [
                lg if hasattr(lg, "dim") else torch.tensor(float(lg))
                for lg in kept_logits
            ]
        )
        if viz_logits.dim() > 1:
            viz_logits = viz_logits.reshape(-1)
        corners = torchvision.ops.box_convert(
            viz_boxes, in_fmt="cxcywh", out_fmt="xyxy"
        )
        keep = torchvision.ops.nms(corners, viz_logits, iou_threshold=NMS_IOU)
        keep = keep[:max_det].tolist()
    except Exception as exc:  # noqa: BLE001
        logger.warning("NMS failed (%s); keeping raw boxes", exc)
        keep = list(range(min(max_det, len(kept_boxes))))

    for i in keep:
        box = kept_boxes[i]
        cx = float(box[0].item() if hasattr(box[0], "item") else box[0])
        cy = float(box[1].item() if hasattr(box[1], "item") else box[1])
        bw = float(box[2].item() if hasattr(box[2], "item") else box[2])
        bh = float(box[3].item() if hasattr(box[3], "item") else box[3])
        x1 = int(max(0, (cx - bw / 2) * iw))
        y1 = int(max(0, (cy - bh / 2) * ih))
        x2 = int(min(iw, (cx + bw / 2) * iw))
        y2 = int(min(ih, (cy + bh / 2) * ih))
        pw, ph = max(1, x2 - x1), max(1, y2 - y1)
        score = float(
            kept_logits[i].item()
            if hasattr(kept_logits[i], "item")
            else kept_logits[i]
        )
        detections.append(
            FastenerDetection(
                index=len(detections),
                label=kept_phrases[i],
                score=score,
                x=x1,
                y=y1,
                w=pw,
                h=ph,
            )
        )
    # Drop "group" boxes that enclose two or more smaller fasteners.
    detections = _drop_multi_object_boxes(detections)
    for i, d in enumerate(detections):
        d.index = i
    return detections or [_full_frame(iw, ih)]


def _drop_multi_object_boxes(
    detections: list[FastenerDetection],
) -> list[FastenerDetection]:
    """
    GroundingDINO often emits one box around ALL screws when the prompt is plural.
    Drop any box whose interior contains the center of 2+ smaller detections.
    """
    if len(detections) < 2:
        return detections
    areas = [max(1, d.w * d.h) for d in detections]
    drop: set[int] = set()
    for i, a in enumerate(detections):
        kids = 0
        for j, b in enumerate(detections):
            if i == j:
                continue
            if areas[j] >= areas[i] * 0.9:
                continue
            cx = b.x + b.w / 2.0
            cy = b.y + b.h / 2.0
            if a.x <= cx <= a.x + a.w and a.y <= cy <= a.y + a.h:
                kids += 1
        if kids >= 2:
            drop.add(i)
            continue
        # Parent wrapping one child + empty space: much larger area + contains child.
        if kids >= 1 and areas[i] >= 1.75 * sorted(areas)[len(areas) // 2]:
            drop.add(i)
    return [d for i, d in enumerate(detections) if i not in drop]


def detect_inventory_parts(
    frame_bytes: bytes,
    *,
    prompt: str | None = None,
    max_det: int = 24,
) -> list[FastenerDetection]:
    """
    Conservative inventory overlay boxes.

    Prefer precision over recall — wood grain / desk speckles must not become
    fasteners. (Inventory *counts* come from whole-frame vision, not these boxes.)
    """
    image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    iw, ih = image.size
    if _get_model() is None:
        return [_full_frame(iw, ih)]

    agent_cap = _singularize_prompt(prompt or DEFAULT_DETECT_PROMPT)
    screw_cap = "hex socket screw . screw . bolt ."
    other_cap = "allen key . hex key . plastic plug . plastic push plug ."
    collected: list[FastenerDetection] = []

    # Higher thresholds than part-ID — low scores = desk noise.
    for cap in (agent_cap, screw_cap, other_cap):
        collected.extend(
            detect_fasteners(
                frame_bytes,
                prompt=cap,
                box_threshold=0.30,
                text_threshold=0.28,
                max_det=max_det,
            )
        )

    # Fewer, stricter column tiles (only to split a screw row — not hunt noise).
    n_cols = 4
    overlap = 0.15
    col_w = iw / float(n_cols)
    for i in range(n_cols):
        x0 = int(max(0, i * col_w - overlap * col_w))
        x1 = int(min(iw, (i + 1) * col_w + overlap * col_w))
        if x1 - x0 < 64:
            continue
        tile = image.crop((x0, 0, x1, ih))
        buf = io.BytesIO()
        tile.save(buf, format="PNG")
        for det in detect_fasteners(
            buf.getvalue(),
            prompt=screw_cap,
            box_threshold=0.28,
            text_threshold=0.26,
            max_det=4,
        ):
            if det.label == "frame":
                continue
            collected.append(
                FastenerDetection(
                    index=0,
                    label=det.label,
                    score=det.score,
                    x=det.x + x0,
                    y=det.y,
                    w=det.w,
                    h=det.h,
                )
            )

    merged = _merge_detections(
        collected,
        max_det=max_det,
        iou_threshold=0.45,
        frame_w=iw,
        frame_h=ih,
        min_score=0.28,
    )
    if not merged:
        return [_full_frame(iw, ih)]
    return merged


def _merge_detections(
    detections: list[FastenerDetection],
    *,
    max_det: int,
    iou_threshold: float,
    frame_w: int | None = None,
    frame_h: int | None = None,
    min_score: float = 0.0,
) -> list[FastenerDetection]:
    """NMS across multipass/tile boxes; drop noise-sized / low-score boxes."""
    cleaned: list[FastenerDetection] = []
    min_px = 28  # absolute floor — wood speckles are tiny
    min_area_frac = 0.0012  # ~0.12% of frame
    frame_area = (frame_w * frame_h) if frame_w and frame_h else None
    for d in detections:
        if d.label == "frame":
            continue
        if d.score < min_score:
            continue
        if d.w < min_px or d.h < min_px:
            continue
        if frame_area and (d.w * d.h) < frame_area * min_area_frac:
            continue
        # Extreme skinny slivers on wood grain
        aspect = max(d.w, d.h) / max(1, min(d.w, d.h))
        if aspect > 8.0 and min(d.w, d.h) < 40:
            continue
        cleaned.append(d)
    if not cleaned:
        return []
    try:
        import torch
        import torchvision

        xyxy = torch.tensor(
            [
                [float(d.x), float(d.y), float(d.x + d.w), float(d.y + d.h)]
                for d in cleaned
            ],
            dtype=torch.float32,
        )
        scores = torch.tensor([float(d.score) for d in cleaned], dtype=torch.float32)
        keep = torchvision.ops.nms(xyxy, scores, iou_threshold).tolist()
        keep = sorted(keep, key=lambda i: scores[i].item(), reverse=True)[:max_det]
    except Exception:  # noqa: BLE001
        order = sorted(range(len(cleaned)), key=lambda i: cleaned[i].score, reverse=True)
        keep = []
        for i in order:
            if len(keep) >= max_det:
                break
            if any(_iou(cleaned[i], cleaned[j]) > iou_threshold for j in keep):
                continue
            keep.append(i)

    out = [cleaned[i] for i in keep]
    out = _drop_multi_object_boxes(out)
    for i, d in enumerate(out):
        d.index = i
    return out


def _iou(a: FastenerDetection, b: FastenerDetection) -> float:
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def crop_detection(
    frame_bytes: bytes, det: FastenerDetection, *, pad: float = 0.08
) -> bytes:
    image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    iw, ih = image.size
    px = int(det.w * pad)
    py = int(det.h * pad)
    left = max(0, det.x - px)
    top = max(0, det.y - py)
    right = min(iw, det.x + det.w + px)
    bottom = min(ih, det.y + det.h + py)
    crop = image.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def detector_name() -> str:
    if _model is not None:
        return "grounding-dino"
    if _model_failed:
        return f"fallback-fullframe({_fail_reason or 'unavailable'})"
    return "uninitialized"


def _full_frame(iw: int, ih: int) -> FastenerDetection:
    return FastenerDetection(
        index=0, label="frame", score=0.0, x=0, y=0, w=iw, h=ih
    )


def _singularize_prompt(prompt: str) -> str:
    """Strip common plurals so DINO does not wrap multiple fasteners in one box."""
    import re

    text = (prompt or "").strip()
    # Word-boundary replacements; keep order longest-first where needed.
    replacements = (
        (r"\bfasteners\b", "fastener"),
        (r"\bscrews\b", "screw"),
        (r"\bbolts\b", "bolt"),
        (r"\bplugs\b", "plug"),
        (r"\brivets\b", "rivet"),
        (r"\bkeys\b", "key"),
    )
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _predict_bytes(
    model: Any,
    frame_bytes: bytes,
    *,
    caption: str,
    box_threshold: float,
    text_threshold: float,
) -> tuple[Any, Any, list[str]]:
    from groundingdino.util.inference import load_image, predict

    suffix = ".jpg"
    if frame_bytes.startswith(b"\x89PNG"):
        suffix = ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(frame_bytes)
        path = tmp.name
    try:
        _source, image = load_image(path)
        boxes, logits, phrases = predict(
            model=model,
            image=image,
            caption=caption,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
            device=_device(),
        )
        return boxes, logits, list(phrases)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "cpu"  # GroundingDINO MPS is flaky; stay on CPU
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _get_model() -> Any:
    global _model, _model_failed, _fail_reason
    if _model is not None:
        return _model
    if _model_failed:
        return None
    try:
        import groundingdino
        from groundingdino.util.inference import load_model

        config = os.path.join(
            os.path.dirname(groundingdino.__file__),
            "config",
            "GroundingDINO_SwinT_OGC.py",
        )
        weights = _ensure_weights()
        model = load_model(config, str(weights), device=_device())
        _model = model
        logger.info("GroundingDINO Swin-T loaded from %s", weights)
        return _model
    except Exception as exc:  # noqa: BLE001
        _model_failed = True
        _fail_reason = str(exc)[:160]
        logger.warning(
            "GroundingDINO unavailable (%s); using full-frame fallback", exc
        )
        return None


def _ensure_weights() -> Path:
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    if WEIGHTS_SWINT.is_file() and WEIGHTS_SWINT.stat().st_size > 1_000_000:
        return WEIGHTS_SWINT
    logger.info("Downloading GroundingDINO weights → %s", WEIGHTS_SWINT)
    import urllib.request

    urllib.request.urlretrieve(WEIGHTS_URL, WEIGHTS_SWINT)  # noqa: S310
    return WEIGHTS_SWINT
