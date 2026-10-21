from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.models.ingest import IngestRequest, IngestResult
from core.services.ingest import IkeaLookupError, IkeaManualIngester, ProductExistsError


def build_ingest_router(ingester: IkeaManualIngester) -> APIRouter:
    router = APIRouter(tags=["ingest"])

    @router.post(
        "/products/ingest",
        response_model=IngestResult,
        summary="Find, download, and process an IKEA assembly manual",
    )
    def ingest_product(body: IngestRequest) -> IngestResult:
        """
        Alexa+/agents call this with a product name (e.g. \"KYRRE\").

        Backend owns web fetch + PDF processing. The agent does not scrape IKEA itself.
        """
        try:
            return ingester.ingest(body)
        except ProductExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except IkeaLookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router
