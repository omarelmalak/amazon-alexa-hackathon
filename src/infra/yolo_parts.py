"""YOLO-World fastener detection for webcam frames."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# Open-vocab prompts covering IKEA hardware the user may hold up.
_FASTENER_CLASSES = [
    "screw",
    "bolt",
    "hex socket screw",
    "allen key",
    "hex key",
    "L-shaped wrench",
    "plastic plug",
    "rivet",
    "fastener",
]

_model: Any = None
_model_failed = False


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
    conf: float = 0.15,
    max_det: int = 8,
) -> list[FastenerDetection]:
    """
    Run YOLO-World on a frame. Returns pixel boxes for each fastener-like object.

    If ultralytics is unavailable, returns a single full-frame box so comparison
    can still run (caller should note detector='fallback').
    """
    image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
    iw, ih = image.size
    model = _get_model()
    if model is None:
        return [_full_frame(iw, ih)]

    try:
        results = model.predict(
            source=image,
            conf=conf,
            verbose=False,
            max_det=max_det,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("YOLO predict failed: %s", exc)
        return [_full_frame(iw, ih)]

    detections: list[FastenerDetection] = []
    if not results:
        return [_full_frame(iw, ih)]
    result = results[0]
    names = result.names or {}
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return [_full_frame(iw, ih)]
    for box in boxes:
        try:
            xyxy = box.xyxy[0].tolist()
            score = float(box.conf[0]) if box.conf is not None else 0.0
            cls_id = int(box.cls[0]) if box.cls is not None else -1
            label = str(names.get(cls_id, "fastener"))
        except Exception:  # noqa: BLE001
            continue
        x1, y1, x2, y2 = (int(v) for v in xyxy)
        x1 = max(0, min(iw - 1, x1))
        y1 = max(0, min(ih - 1, y1))
        x2 = max(0, min(iw, x2))
        y2 = max(0, min(ih, y2))
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        # Drop tiny noise blobs
        if bw * bh < (iw * ih) * 0.002:
            continue
        detections.append(
            FastenerDetection(
                index=len(detections),
                label=label,
                score=score,
                x=x1,
                y=y1,
                w=bw,
                h=bh,
            )
        )
    return detections or [_full_frame(iw, ih)]


def _full_frame(iw: int, ih: int) -> FastenerDetection:
    return FastenerDetection(
        index=0,
        label="frame",
        score=0.0,
        x=0,
        y=0,
        w=iw,
        h=ih,
    )


def crop_detection(frame_bytes: bytes, det: FastenerDetection, *, pad: float = 0.08) -> bytes:
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
        return "yolo-world"
    if _model_failed:
        return "fallback-fullframe"
    return "uninitialized"


def _get_model() -> Any:
    global _model, _model_failed
    if _model is not None:
        return _model
    if _model_failed:
        return None
    try:
        from ultralytics import YOLOWorld

        model = YOLOWorld("yolov8s-worldv2.pt")
        model.set_classes(_FASTENER_CLASSES)
        _model = model
        logger.info("YOLO-World loaded with classes %s", _FASTENER_CLASSES)
        return _model
    except Exception as exc:  # noqa: BLE001
        _model_failed = True
        logger.warning("YOLO-World unavailable (%s); using full-frame fallback", exc)
        return None
