from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from core.models.catalog import ProductManifest
from core.models.ingest import IngestRequest, IngestResult
from infra.ikea_fetch import IkeaLookupError, download_pdf, find_manual
from infra.manifest_enrich import EnrichmentError, enrich_manifest_with_vision
from infra.pdf_manual import prepare_product_from_pdf


class ProductExistsError(RuntimeError):
    pass


def slugify_product_id(query: str) -> str:
    """Turn 'KYRRE stool' / 'kyrre' into a stable folder id."""
    token = query.strip().lower()
    # Prefer the first ALLCAPS-like series token if present in original query.
    series = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", query)
    if series:
        token = series[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", token).strip("-")
    return slug or "product"


class IkeaManualIngester:
    """Search IKEA → download PDF → slice diagrams → optional LLM curation."""

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root.resolve()

    def ingest(self, request: IngestRequest) -> IngestResult:
        product_id = (request.product_id or slugify_product_id(request.query)).lower()
        product_dir = self._data_root / product_id
        manifest_path = product_dir / "manifest.json"

        if manifest_path.exists() and not request.force:
            raise ProductExistsError(
                f"Product '{product_id}' already exists. "
                "Pass force=true to re-download and reprocess."
            )

        if request.force and manifest_path.exists():
            # Avoid merging stale curated fields into a fresh download.
            manifest_path.unlink()

        with httpx.Client(
            follow_redirects=True,
            timeout=90.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; FlatpackCoPilot/0.1; "
                    "+https://github.com/omarelmalak/amazon-alexa-hackathon)"
                )
            },
        ) as client:
            manual = find_manual(
                request.query,
                market=request.market,
                locale=request.locale,
                client=client,
            )
            pdf_path = product_dir / "source" / "manual.pdf"
            download_pdf(manual.pdf_url, pdf_path, client=client)

        display_name = (
            manual.label
            or manual.product.name
            or product_id.upper()
        )
        # Prefer series name alone when label is like "KYRRE Stool".
        series_match = re.match(r"^([A-ZÀ-ÖØ-Þ]{3,})\b", display_name.strip())
        if series_match:
            display_name = series_match.group(1)

        manifest = prepare_product_from_pdf(
            pdf_path,
            product_dir,
            product_id=product_id,
            display_name=display_name,
        )

        enriched = False
        skip_reason: str | None = None
        if request.enrich:
            try:
                # Fresh slice should not preserve stale curated fields when force=true:
                # prepare_product_from_pdf merges with existing — delete first if force.
                manifest = enrich_manifest_with_vision(product_dir, manifest)
                (product_dir / "manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n",
                    encoding="utf-8",
                )
                enriched = True
            except EnrichmentError as exc:
                skip_reason = str(exc)
            except Exception as exc:  # noqa: BLE001 — surface to API caller
                skip_reason = f"LLM enrichment failed: {exc}"

        validated = ProductManifest.model_validate(manifest)
        return IngestResult(
            productId=validated.productId,
            displayName=validated.displayName,
            sourceQuery=request.query,
            ikeaItemNo=manual.product.item_no,
            productUrl=manual.product.product_url,
            pdfUrl=manual.pdf_url,
            documentId=manual.document_id,
            enriched=enriched,
            enrichmentSkippedReason=skip_reason,
            stepCount=len(validated.steps),
            bomItemCount=len(validated.bom),
            manifest=validated,
        )


# Re-export lookup error for adapters
__all__ = [
    "IkeaManualIngester",
    "IkeaLookupError",
    "ProductExistsError",
    "slugify_product_id",
]
