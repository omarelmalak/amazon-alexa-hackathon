from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from core.models.catalog import ProductManifest
from core.models.ingest import IngestRequest, IngestResult
from infra.ikea_fetch import IkeaLookupError, download_pdf, find_manual
from infra.manifest_enrich import EnrichmentError, enrich_manifest_with_vision
from infra.parts_library import populate_parts_from_bom
from infra.pdf_manual import prepare_product_from_pdf


class ProductExistsError(RuntimeError):
    pass


class InvalidProductQueryError(ValueError):
    """Spoken text is not a specific IKEA product/series name."""


# Common English words that must never become a product folder id.
_QUERY_STOPWORDS = frozenset(
    """
    a an the and or but if so to of in on for from with about into over after
    i me my we you your they them their it its this that these those
    want wanted wanting need needed needing help helping build building built
    assemble assembling assembly furniture piece product item something anything
    everything nothing ikea just got get getting have has had bought buy buying
    new old can could would should will shall please thanks thank hey hi hello
    how what which when where who why yes no okay ok sure start starting
    guide guiding next step steps show tell ask look looking right wrong
    """.split()
)


def extract_product_token(query: str) -> str | None:
    """
    Prefer an IKEA-like series token (e.g. KYRRE, BESTÅ). Reject bare English words
    so 'I want to build something' never becomes productId 'want'.
    """
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{2,}", query)
    if not tokens:
        return None

    # Prefer ALL-CAPS / accented series names of length >= 3 (IKEA style).
    for tok in tokens:
        if len(tok) >= 3 and tok.isupper() and tok.lower() not in _QUERY_STOPWORDS:
            return tok.lower()

    # Otherwise take the first non-stopword token that looks like a name.
    for tok in tokens:
        low = tok.lower()
        if low in _QUERY_STOPWORDS:
            continue
        if len(low) < 3:
            continue
        # Reject sentences: if most tokens are stopwords, require a stronger hit.
        return low

    return None


def slugify_product_id(query: str) -> str:
    """Turn 'KYRRE stool' / 'kyrre' into a stable folder id."""
    token = extract_product_token(query)
    if not token:
        raise InvalidProductQueryError(
            "No specific IKEA product name found. Ask the user which piece "
            "(e.g. KYRRE, RISATORP, BESTÅ) before calling ingest again."
        )
    slug = re.sub(r"[^a-z0-9]+", "-", token).strip("-")
    return slug or "product"


def validate_ingest_query(query: str, product_id: str | None = None) -> None:
    """Raise if the spoken query is too vague to ingest."""
    q = (query or "").strip()
    if not q:
        raise InvalidProductQueryError(
            "Empty product query. Ask the user for the IKEA product name."
        )
    if product_id and product_id.strip():
        # Explicit id is trusted if it isn't a stopword.
        if product_id.strip().lower() in _QUERY_STOPWORDS:
            raise InvalidProductQueryError(
                f"'{product_id}' is not a valid IKEA product id."
            )
        return
    if extract_product_token(q) is None:
        raise InvalidProductQueryError(
            "That does not look like a specific IKEA product name. "
            "Ask which piece they have (series name on the box, e.g. KYRRE) "
            "before calling ingest_product."
        )


class IkeaManualIngester:
    """Search IKEA → download PDF → slice diagrams → optional LLM curation."""

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root.resolve()

    def ingest(self, request: IngestRequest) -> IngestResult:
        validate_ingest_query(request.query, request.product_id)
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

        # Populate data/<product>/parts/{partId}.png from BOM crops (skip if present).
        # Real photos dropped into that folder win — we never overwrite without force.
        try:
            populate_parts_from_bom(
                product_dir,
                validated.bom,
                force=False,
            )
        except Exception as exc:  # noqa: BLE001 — ingest still succeeds
            if skip_reason:
                skip_reason = f"{skip_reason}; parts library: {exc}"
            else:
                skip_reason = f"parts library: {exc}"

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
    "InvalidProductQueryError",
    "extract_product_token",
    "slugify_product_id",
    "validate_ingest_query",
]
