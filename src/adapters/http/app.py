from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from adapters.http.routes_catalog import build_catalog_router
from adapters.http.routes_ingest import build_ingest_router
from adapters.http.routes_sessions import build_sessions_router
from core.services.catalog import FilesystemProductCatalog
from core.services.ingest import IkeaManualIngester
from core.services.ordering import MockStripePartsOrdering
from core.services.session import InMemoryAssemblySession
from core.services.vision import CohereVisionInspector

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"

load_dotenv(ROOT / ".env")


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or DEFAULT_DATA_ROOT
    catalog = FilesystemProductCatalog(root)
    ingester = IkeaManualIngester(root)
    sessions = InMemoryAssemblySession(catalog)
    vision = CohereVisionInspector()
    ordering = MockStripePartsOrdering()
    app = FastAPI(
        title="IKEA Flatpack Co-Pilot",
        version="0.4.0",
        description=(
            "Catalog, ingest, sessions, Cohere vision, Tier-1 webcam UI, "
            "and mock replacement-part checkout."
        ),
    )
    app.include_router(build_catalog_router(catalog), prefix="/v1")
    app.include_router(build_ingest_router(ingester), prefix="/v1")
    app.include_router(
        build_sessions_router(catalog, sessions, vision, ordering), prefix="/v1"
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

        @app.get("/")
        def assembly_ui() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
