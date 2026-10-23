"""Local part-photo library under data/<productId>/parts/{partId}.png."""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

PARTS_SUBDIR = "parts"
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


class PartImageNotFoundError(LookupError):
    pass


def parts_dir(product_dir: Path) -> Path:
    return product_dir / PARTS_SUBDIR


def ensure_parts_dir(product_dir: Path) -> Path:
    path = parts_dir(product_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_part_image_ids(product_dir: Path) -> list[str]:
    root = parts_dir(product_dir)
    if not root.is_dir():
        return []
    ids: set[str] = set()
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_EXTS:
            continue
        ids.add(path.stem)
    return sorted(ids)


def part_image_path(product_dir: Path, part_id: str) -> Path | None:
    root = parts_dir(product_dir)
    if not root.is_dir():
        return None
    stem = str(part_id).strip()
    for ext in _IMAGE_EXTS:
        candidate = root / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def get_part_image_bytes(product_dir: Path, part_id: str) -> bytes:
    path = part_image_path(product_dir, part_id)
    if path is None:
        raise PartImageNotFoundError(
            f"No part photo for '{part_id}' under {parts_dir(product_dir)}. "
            "Add data/<product>/parts/{partId}.png (exact hardware photo or BOM crop)."
        )
    return path.read_bytes()


def save_part_image(
    product_dir: Path,
    part_id: str,
    image_bytes: bytes,
    *,
    source: str = "manual",
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write PNG + sidecar JSON. Overwrites existing image for that partId."""
    ensure_parts_dir(product_dir)
    png = _ensure_png(image_bytes)
    out = parts_dir(product_dir) / f"{part_id}.png"
    out.write_bytes(png)
    sidecar = parts_dir(product_dir) / f"{part_id}.json"
    payload = {
        "partId": part_id,
        "source": source,
        "bytes": len(png),
        **(meta or {}),
    }
    sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def load_reference_images(
    product_dir: Path, part_ids: list[str]
) -> dict[str, bytes]:
    refs: dict[str, bytes] = {}
    for pid in part_ids:
        path = part_image_path(product_dir, pid)
        if path is not None:
            refs[pid] = path.read_bytes()
    return refs


def populate_parts_from_bom(
    product_dir: Path,
    bom: list[Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Crop each BOM part illustration from diagrams/bom.png into parts/{partId}.png.

    Skips parts that already have a photo unless force=True. Manual uploads of
    real screw photos into parts/ are preferred when available.
    """
    bom_path = product_dir / "diagrams" / "bom.png"
    if not bom_path.is_file():
        return {
            "ok": False,
            "reason": "diagrams/bom.png missing",
            "saved": [],
            "skipped": [],
        }

    ensure_parts_dir(product_dir)
    part_rows = []
    for item in bom:
        if hasattr(item, "partId"):
            part_rows.append(
                {
                    "partId": str(item.partId),
                    "name": getattr(item, "name", None) or "",
                }
            )
        elif isinstance(item, dict):
            part_rows.append(
                {
                    "partId": str(item.get("partId") or ""),
                    "name": str(item.get("name") or ""),
                }
            )
    part_rows = [p for p in part_rows if p["partId"]]

    saved: list[str] = []
    skipped: list[str] = []
    for row in part_rows:
        pid = row["partId"]
        existing = part_image_path(product_dir, pid)
        if existing is not None and not force:
            skipped.append(pid)
            continue

    need = [p for p in part_rows if p["partId"] not in skipped]
    if not need:
        return {"ok": True, "saved": saved, "skipped": skipped, "source": "existing"}

    bom_bytes = bom_path.read_bytes()
    boxes = _locate_bom_part_boxes(
        bom_bytes, need, api_key=api_key, model=model
    )
    image = Image.open(io.BytesIO(bom_bytes)).convert("RGB")
    w, h = image.size

    for row in need:
        pid = row["partId"]
        box = boxes.get(pid)
        if not box:
            continue
        x, y, bw, bh = box
        # Generous pad so heads, tips, and labels stay inside the crop.
        pad_x = max(16, int(bw * 0.28))
        pad_y = max(16, int(bh * 0.18))
        left = max(0, x - pad_x)
        top = max(0, y - pad_y)
        right = min(w, x + bw + pad_x)
        bottom = min(h, y + bh + pad_y)
        if right <= left or bottom <= top:
            continue
        crop = image.crop((left, top, right, bottom))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        save_part_image(
            product_dir,
            pid,
            buf.getvalue(),
            source="bom_crop",
            meta={"name": row.get("name"), "box": {"x": x, "y": y, "w": bw, "h": bh}},
        )
        saved.append(pid)

    return {
        "ok": True,
        "saved": saved,
        "skipped": skipped,
        "missing": [p["partId"] for p in need if p["partId"] not in saved],
        "source": "bom_crop",
    }


def annotate_detections(
    frame_bytes: bytes,
    detections: list[dict[str, Any]],
    *,
    winner_index: int | None = None,
) -> bytes:
    """Draw numbered boxes on a frame; highlight the winning detection."""
    image = Image.open(io.BytesIO(_ensure_png(frame_bytes))).convert("RGB")
    draw = ImageDraw.Draw(image)
    for i, det in enumerate(detections):
        box = det.get("box") or {}
        x = int(box.get("x", 0))
        y = int(box.get("y", 0))
        bw = int(box.get("w", 0))
        bh = int(box.get("h", 0))
        is_win = winner_index is not None and i == winner_index
        color = (46, 204, 113) if is_win else (52, 152, 219)
        width = 4 if is_win else 2
        draw.rectangle([x, y, x + bw, y + bh], outline=color, width=width)
        label = det.get("label") or f"#{i + 1}"
        score = det.get("score")
        text = f"{label}" if score is None else f"{label} {float(score):.2f}"
        draw.rectangle([x, max(0, y - 18), x + 8 * len(text), y], fill=color)
        draw.text((x + 2, max(0, y - 16)), text, fill=(255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def bytes_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _ensure_png(image_bytes: bytes) -> bytes:
    if image_bytes.startswith(b"\x89PNG"):
        return image_bytes
    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _locate_bom_part_boxes(
    bom_png: bytes,
    parts: list[dict[str, str]],
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """
    Column-split the bottom inventory strip (IKEA BOM convention).

    Vision box proposals on full manuals are unreliable (they latch onto safety
    cartoons). Prefer geometry; users can replace parts/{id}.png with real photos.
    """
    _ = api_key, model  # reserved for future spare-part image fetch
    return _heuristic_bom_boxes(bom_png, parts)


def _heuristic_bom_boxes(
    bom_png: bytes, parts: list[dict[str, str]]
) -> dict[str, tuple[int, int, int, int]]:
    """Bottom inventory strip, N equal columns (typical IKEA BOM layout)."""
    image = Image.open(io.BytesIO(bom_png))
    w, h = image.size
    # Include fastener heads (above the shaft) — critical for screw ID.
    top = int(h * 0.60)
    bottom = int(h * 0.93)
    band_h = max(40, bottom - top)
    n = max(1, len(parts))
    out: dict[str, tuple[int, int, int, int]] = {}
    for i, row in enumerate(parts):
        # Wide column so the full drawing + part number stay in frame.
        col = 0.92 / n
        left = int(w * (0.03 + i * col))
        width = int(w * col * 0.82)
        out[row["partId"]] = (left, top, width, band_h)
    return out


def parse_part_id(value: str) -> str | None:
    m = re.search(r"(\d{5,})", value or "")
    return m.group(1) if m else None
