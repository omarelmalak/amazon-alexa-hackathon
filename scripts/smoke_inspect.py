#!/usr/bin/env python3
"""Phase 2 smoke test: diagram-as-frame should look aligned; flipped should not."""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from core.services.catalog import FilesystemProductCatalog  # noqa: E402
from core.services.vision import CohereVisionInspector  # noqa: E402


def main() -> None:
    product_id = sys.argv[1] if len(sys.argv) > 1 else "kyrre"
    step_number = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    catalog = FilesystemProductCatalog(ROOT / "data")
    step = catalog.get_step(product_id, step_number)
    diagram = catalog.get_diagram(product_id, step_number)
    vision = CohereVisionInspector()

    print(f"Product={product_id} step={step_number} title={step.title!r}")
    print("--- same diagram as webcam (expect aligned≈true) ---")
    ok = vision.verify_orientation(
        frame_bytes=diagram,
        diagram_bytes=diagram,
        step=step,
        product_id=product_id,
    )
    print(ok.model_dump_json(indent=2))

    # Obvious negative: red X over the frame (rotation alone is unreliable on
    # symmetric IKEA line art with Command A Vision).
    from PIL import ImageDraw

    img = Image.open(io.BytesIO(diagram)).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.line((0, 0, img.width, img.height), fill=(255, 0, 0), width=40)
    draw.line((0, img.height, img.width, 0), fill=(255, 0, 0), width=40)
    draw.rectangle((20, 20, img.width - 20, img.height - 20), outline=(255, 0, 0), width=30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    wrong = buf.getvalue()
    label = "scribbled / X-marked diagram as webcam"

    print(f"--- {label} (expect aligned≈false) ---")
    bad = vision.verify_orientation(
        frame_bytes=wrong,
        diagram_bytes=diagram,
        step=step,
        product_id=product_id,
    )
    print(bad.model_dump_json(indent=2))

    b64 = base64.b64encode(diagram).decode("ascii")
    print(f"\nframeBase64 length for diagram: {len(b64)} chars")
    print(
        f"\nsummary: same_aligned={ok.aligned} negative_aligned={bad.aligned}"
    )


if __name__ == "__main__":
    main()
