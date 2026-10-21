from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from adapters.http.routes_catalog import build_catalog_router
from adapters.http.routes_ingest import build_ingest_router
from core.services.catalog import FilesystemProductCatalog
from core.services.ingest import IkeaManualIngester

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = ROOT / "data"


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or DEFAULT_DATA_ROOT
    catalog = FilesystemProductCatalog(root)
    ingester = IkeaManualIngester(root)
    app = FastAPI(
        title="IKEA Flatpack Co-Pilot",
        version="0.1.0",
        description=(
            "Catalog + manual ingest API. Core interfaces behind FastAPI; "
            "MCP adapters come later for Alexa+."
        ),
    )
    app.include_router(build_catalog_router(catalog), prefix="/v1")
    app.include_router(build_ingest_router(ingester), prefix="/v1")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
