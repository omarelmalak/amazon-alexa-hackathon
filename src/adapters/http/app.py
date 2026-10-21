from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from adapters.http.routes_catalog import build_catalog_router
from adapters.http.routes_ingest import build_ingest_router
from adapters.http.routes_sessions import build_sessions_router
from core.services.catalog import FilesystemProductCatalog
from core.services.ingest import IkeaManualIngester
from core.services.session import InMemoryAssemblySession
from core.services.vision import CohereVisionInspector

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = ROOT / "data"

load_dotenv(ROOT / ".env")


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or DEFAULT_DATA_ROOT
    catalog = FilesystemProductCatalog(root)
    ingester = IkeaManualIngester(root)
    sessions = InMemoryAssemblySession(catalog)
    vision = CohereVisionInspector()
    app = FastAPI(
        title="IKEA Flatpack Co-Pilot",
        version="0.2.0",
        description=(
            "Catalog, ingest, sessions, and Cohere vision inspect. "
            "Core interfaces behind FastAPI; MCP adapters come later for Alexa+."
        ),
    )
    app.include_router(build_catalog_router(catalog), prefix="/v1")
    app.include_router(build_ingest_router(ingester), prefix="/v1")
    app.include_router(
        build_sessions_router(catalog, sessions, vision), prefix="/v1"
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
