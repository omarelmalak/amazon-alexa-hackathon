from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops

PART_ID_RE = re.compile(r"^\d{6}$")
QTY_RE = re.compile(r"^(\d+)\s*x$", re.IGNORECASE)
STEP_NUM_RE = re.compile(r"^\d{1,2}$")


@dataclass
class PageAnalysis:
    page_index: int  # 0-based
    page_number: int  # 1-based
    kind: str  # cover | bom | step | unknown
    step_number: int | None = None
    part_ids: list[str] | None = None
    quantities: list[int] | None = None


def _page_texts(page: pymupdf.Page) -> list[tuple[float, float, str]]:
    """Return (y, x, text) tokens from page blocks."""
    texts: list[tuple[float, float, str]] = []
    for block in page.get_text("blocks"):
        x0, y0, _x1, _y1, text, *_ = block
        for line in str(text).splitlines():
            cleaned = line.strip()
            if cleaned:
                texts.append((y0, x0, cleaned))
    return texts


def _parts_and_qtys_spatial(
    texts: list[tuple[float, float, str]],
) -> tuple[list[str], list[int]]:
    """Sort part IDs and quantities left-to-right for BOM pairing."""
    parts = sorted(
        ((x, t) for _y, x, t in texts if PART_ID_RE.match(t)),
        key=lambda item: item[0],
    )
    qtys = sorted(
        (
            (x, int(m.group(1)))
            for _y, x, t in texts
            if (m := QTY_RE.match(t))
        ),
        key=lambda item: item[0],
    )
    # When quantities share one text block, x positions collapse — keep doc order.
    if len(qtys) <= 1:
        qtys = [
            (idx, int(m.group(1)))
            for idx, (_y, _x, t) in enumerate(texts)
            if (m := QTY_RE.match(t))
        ]
    return [p[1] for p in parts], [q[1] for q in qtys]


def analyze_page(page: pymupdf.Page, page_index: int) -> PageAnalysis:
    texts = _page_texts(page)
    part_ids, quantities = _parts_and_qtys_spatial(texts)

    # Large step number is typically near the top-left of assembly pages.
    top_left_candidates = [
        int(t)
        for y, x, t in texts
        if STEP_NUM_RE.match(t) and y < 100 and x < 80 and 1 <= int(t) <= 40
    ]

    page_number = page_index + 1
    if page_index == 0 and len(part_ids) == 0:
        return PageAnalysis(page_index, page_number, "cover")

    if len(part_ids) >= 2 and len(quantities) >= 2 and not top_left_candidates:
        return PageAnalysis(
            page_index,
            page_number,
            "bom",
            part_ids=part_ids,
            quantities=quantities,
        )

    if top_left_candidates:
        return PageAnalysis(
            page_index,
            page_number,
            "step",
            step_number=top_left_candidates[0],
            part_ids=part_ids,
            quantities=quantities,
        )

    return PageAnalysis(
        page_index,
        page_number,
        "unknown",
        part_ids=part_ids,
        quantities=quantities,
    )


def render_page_png(page: pymupdf.Page, dpi: int = 200) -> bytes:
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return pix.tobytes("png")


def trim_whitespace(png_bytes: bytes, padding: int = 8) -> bytes:
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    bg = Image.new("RGB", image.size, (255, 255, 255))
    diff = ImageChops.difference(image, bg)
    bbox = diff.getbbox()
    if not bbox:
        return png_bytes
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    cropped = image.crop((left, top, right, bottom))
    out = io.BytesIO()
    cropped.save(out, format="PNG")
    return out.getvalue()


def _normalize_ikea_page_kinds(analyses: list[PageAnalysis]) -> list[PageAnalysis]:
    """
    IKEA manuals are usually: cover → BOM → numbered steps.

    Web PDFs often draw part numbers as outlines (no extractable text), so
    heuristic classification can mark BOM/step pages as 'unknown'. Repair the
    common layout so diagrams still land in the right buckets.
    """
    if not analyses:
        return analyses

    fixed = list(analyses)
    if fixed[0].kind == "cover" and len(fixed) > 1 and fixed[1].kind == "unknown":
        fixed[1] = PageAnalysis(
            page_index=fixed[1].page_index,
            page_number=fixed[1].page_number,
            kind="bom",
            part_ids=fixed[1].part_ids or [],
            quantities=fixed[1].quantities or [],
        )

    next_step = 1
    for i, analysis in enumerate(fixed):
        if analysis.kind == "step":
            next_step = (analysis.step_number or next_step) + 1
            continue
        if analysis.kind != "unknown":
            continue
        # Trailing unknown pages after cover/BOM → assembly steps.
        if i >= 1:
            fixed[i] = PageAnalysis(
                page_index=analysis.page_index,
                page_number=analysis.page_number,
                kind="step",
                step_number=next_step,
                part_ids=analysis.part_ids or [],
                quantities=analysis.quantities or [],
            )
            next_step += 1
    return fixed


def prepare_product_from_pdf(
    pdf_path: Path,
    product_dir: Path,
    *,
    product_id: str,
    display_name: str,
    dpi: int = 200,
    trim: bool = True,
) -> dict:
    """
    Rasterize an IKEA-style PDF into diagrams/ + scaffold manifest.json.

    Hardware names and titles are left sparse for manual curation.
    """
    pdf_path = pdf_path.resolve()
    product_dir = product_dir.resolve()
    source_dir = product_dir / "source"
    diagrams_dir = product_dir / "diagrams"
    source_dir.mkdir(parents=True, exist_ok=True)
    if diagrams_dir.exists():
        for stale in diagrams_dir.glob("*"):
            if stale.is_file():
                stale.unlink()
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    dest_pdf = source_dir / "manual.pdf"
    if pdf_path != dest_pdf:
        dest_pdf.write_bytes(pdf_path.read_bytes())

    doc = pymupdf.open(pdf_path)
    analyses = _normalize_ikea_page_kinds(
        [analyze_page(doc[i], i) for i in range(doc.page_count)]
    )

    bom_parts: list[dict] = []
    steps: list[dict] = []
    step_counter = 0

    for analysis in analyses:
        page = doc[analysis.page_index]
        png = render_page_png(page, dpi=dpi)
        if trim:
            png = trim_whitespace(png)

        if analysis.kind == "cover":
            (diagrams_dir / "cover.png").write_bytes(png)
            continue

        if analysis.kind == "bom":
            (diagrams_dir / "bom.png").write_bytes(png)
            # Best-effort zip of part ids to quantities by document order.
            part_ids = analysis.part_ids or []
            qtys = analysis.quantities or []
            for idx, part_id in enumerate(part_ids):
                qty = qtys[idx] if idx < len(qtys) else 0
                bom_parts.append({"partId": part_id, "name": "", "qty": qty})
            continue

        if analysis.kind == "step":
            step_counter += 1
            number = analysis.step_number or step_counter
            filename = f"{number:02d}.png"
            (diagrams_dir / filename).write_bytes(png)
            required: list[dict] = []
            part_ids = analysis.part_ids or []
            qtys = analysis.quantities or []
            if len(part_ids) == 1 and len(qtys) == 1:
                required.append(
                    {"partId": part_ids[0], "name": "", "qty": qtys[0]}
                )
            elif part_ids:
                # Prefer pairing first qty with first fastener when a tool id is present.
                if len(qtys) == 1 and len(part_ids) >= 1:
                    required.append(
                        {"partId": part_ids[0], "name": "", "qty": qtys[0]}
                    )
                    for extra in part_ids[1:]:
                        required.append({"partId": extra, "name": "", "qty": 0})
                else:
                    for idx, part_id in enumerate(part_ids):
                        qty = qtys[idx] if idx < len(qtys) else 0
                        required.append({"partId": part_id, "name": "", "qty": qty})

            steps.append(
                {
                    "number": number,
                    "diagram": f"diagrams/{filename}",
                    "title": f"Step {number}",
                    "requiredHardware": required,
                    "completionHints": [],
                }
            )
            continue

        # Unknown pages: store as extras for manual review.
        extra_name = f"page_{analysis.page_number:02d}.png"
        (diagrams_dir / extra_name).write_bytes(png)

    steps.sort(key=lambda s: s["number"])

    # If BOM pairing looked wrong, rebuild totals from steps when BOM empty.
    if not bom_parts:
        totals: dict[str, int] = {}
        for step in steps:
            for hw in step["requiredHardware"]:
                if hw["qty"] > 0:
                    totals[hw["partId"]] = totals.get(hw["partId"], 0) + hw["qty"]
        bom_parts = [
            {"partId": pid, "name": "", "qty": qty} for pid, qty in totals.items()
        ]

    manifest = {
        "productId": product_id,
        "displayName": display_name,
        "manualSource": "source/manual.pdf",
        "bom": bom_parts,
        "steps": steps,
    }

    manifest_path = product_dir / "manifest.json"
    if manifest_path.exists():
        # Preserve curated fields (names, titles, hints) when re-running.
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = _merge_manifest(existing, manifest)

    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _merge_manifest(existing: dict, fresh: dict) -> dict:
    """Keep human-curated names/titles/hints; refresh diagram paths and structure."""
    existing_bom = {item["partId"]: item for item in existing.get("bom", [])}
    merged_bom = []
    for item in fresh.get("bom", []):
        old = existing_bom.get(item["partId"], {})
        merged_bom.append(
            {
                "partId": item["partId"],
                "name": old.get("name") or item.get("name") or "",
                "qty": item.get("qty", old.get("qty", 0)),
            }
        )

    existing_steps = {s["number"]: s for s in existing.get("steps", [])}
    merged_steps = []
    for step in fresh.get("steps", []):
        old = existing_steps.get(step["number"], {})
        old_hw = {h["partId"]: h for h in old.get("requiredHardware", [])}
        required = []
        for hw in step.get("requiredHardware", []):
            prev = old_hw.get(hw["partId"], {})
            required.append(
                {
                    "partId": hw["partId"],
                    "name": prev.get("name") or hw.get("name") or "",
                    "qty": hw.get("qty", prev.get("qty", 0)),
                }
            )
        merged_steps.append(
            {
                "number": step["number"],
                "diagram": step["diagram"],
                "title": old.get("title") or step.get("title") or f"Step {step['number']}",
                "requiredHardware": required,
                "completionHints": old.get("completionHints")
                or step.get("completionHints")
                or [],
            }
        )

    return {
        "productId": fresh.get("productId") or existing.get("productId"),
        "displayName": existing.get("displayName") or fresh.get("displayName"),
        "manualSource": fresh.get("manualSource") or existing.get("manualSource"),
        "bom": merged_bom,
        "steps": merged_steps,
    }
