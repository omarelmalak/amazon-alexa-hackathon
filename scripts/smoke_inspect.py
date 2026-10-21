#!/usr/bin/env python3
"""Phase 2 smoke test: diagram-as-frame should look aligned; scribbled should not.

Does NOT need the FastAPI server — calls CohereVisionInspector directly.

  PYTHONPATH=src python scripts/smoke_inspect.py kyrre 1
  PYTHONPATH=src python scripts/smoke_inspect.py kyrre 1 --debug
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from core.services.catalog import FilesystemProductCatalog  # noqa: E402
from core.services.vision import CohereVisionInspector  # noqa: E402


def _dump(debug_dir: Path | None, name: str, png_bytes: bytes) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / name
    path.write_bytes(png_bytes)
    print(f"  wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product_id", nargs="?", default="kyrre")
    parser.add_argument("step_number", nargs="?", type=int, default=1)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write the exact Image A/B PNGs sent to Cohere under data/_debug_inspect/",
    )
    args = parser.parse_args()

    debug_dir = ROOT / "data" / "_debug_inspect" if args.debug else None

    catalog = FilesystemProductCatalog(ROOT / "data")
    step = catalog.get_step(args.product_id, args.step_number)
    diagram = catalog.get_diagram(args.product_id, args.step_number)
    vision = CohereVisionInspector()

    print(f"Product={args.product_id} step={args.step_number} title={step.title!r}")
    print("(no API server required — direct VisionInspector call)\n")

    print("--- case 1: same diagram as webcam (expect aligned≈true) ---")
    print("  Image A (webcam) = diagram PNG")
    print("  Image B (manual) = same diagram PNG")
    _dump(debug_dir, "case1_imageA_webcam.png", diagram)
    _dump(debug_dir, "case1_imageB_diagram.png", diagram)
    ok = vision.verify_orientation(
        frame_bytes=diagram,
        diagram_bytes=diagram,
        step=step,
        product_id=args.product_id,
    )
    print(ok.model_dump_json(indent=2))

    img = Image.open(io.BytesIO(diagram)).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.line((0, 0, img.width, img.height), fill=(255, 0, 0), width=40)
    draw.line((0, img.height, img.width, 0), fill=(255, 0, 0), width=40)
    draw.rectangle(
        (20, 20, img.width - 20, img.height - 20), outline=(255, 0, 0), width=30
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    wrong = buf.getvalue()

    print("\n--- case 2: red-X scribbled diagram as webcam (expect aligned≈false) ---")
    print("  Image A (webcam) = diagram with red X overlay")
    print("  Image B (manual) = clean diagram PNG")
    _dump(debug_dir, "case2_imageA_webcam.png", wrong)
    _dump(debug_dir, "case2_imageB_diagram.png", diagram)
    bad = vision.verify_orientation(
        frame_bytes=wrong,
        diagram_bytes=diagram,
        step=step,
        product_id=args.product_id,
    )
    print(bad.model_dump_json(indent=2))

    b64 = base64.b64encode(diagram).decode("ascii")
    print(f"\nframeBase64 length for diagram: {len(b64)} chars")
    print(f"summary: same_aligned={ok.aligned} negative_aligned={bad.aligned}")
    if debug_dir is not None:
        print(f"\nOpen the PNGs in: {debug_dir}")


if __name__ == "__main__":
    main()
