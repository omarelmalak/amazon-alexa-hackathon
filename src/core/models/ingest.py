from __future__ import annotations

from pydantic import BaseModel, Field

from core.models.catalog import ProductManifest


class IngestRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        description="IKEA product series or name, e.g. 'KYRRE' or 'kyrre stool'",
    )
    market: str = Field(default="us", description="IKEA market code")
    locale: str = Field(default="en", description="IKEA locale")
    product_id: str | None = Field(
        default=None,
        description="Override slug under data/; default = normalized series name",
    )
    force: bool = Field(
        default=False,
        description="Overwrite an existing product package if present",
    )
    enrich: bool = Field(
        default=True,
        description="Use vision LLM to fill part names, titles, completionHints",
    )


class IngestResult(BaseModel):
    productId: str
    displayName: str
    sourceQuery: str
    ikeaItemNo: str
    productUrl: str
    pdfUrl: str
    documentId: str | None = None
    enriched: bool
    enrichmentSkippedReason: str | None = None
    stepCount: int
    bomItemCount: int
    manifest: ProductManifest
