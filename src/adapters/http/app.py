from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from adapters.http.routes_catalog import build_catalog_router
from adapters.http.routes_ingest import build_ingest_router
from adapters.http.routes_sessions import build_sessions_router
from adapters.http.routes_sim import build_sim_router
from adapters.mcp import create_mcp_server, mount_mcp
from core.services.catalog import FilesystemProductCatalog
from core.services.ingest import IkeaManualIngester
from core.services.ordering import MockIkeaSparePartsOrdering
from core.services.session import InMemoryAssemblySession
from core.services.vision import CohereVisionInspector

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"
WEB_DIST = ROOT / "web" / "dist"
DEMO_FRAMES_DIR = ROOT / "data" / "_debug_inspect"

load_dotenv(ROOT / ".env")


def create_app(data_root: Path | None = None) -> FastAPI:
    root = data_root or DEFAULT_DATA_ROOT
    catalog = FilesystemProductCatalog(root)
    ingester = IkeaManualIngester(root)
    sessions = InMemoryAssemblySession(catalog)
    vision = CohereVisionInspector()
    ordering = MockIkeaSparePartsOrdering()
    app = FastAPI(
        title="IKEA Flatpack Co-Pilot",
        version="0.7.0",
        description=(
            "Catalog, ingest, sessions, Cohere vision, Alexa+ demo UI with debug mode "
            "(camera + MCP wire), simulated IKEA spare-parts checkout, and Streamable HTTP MCP at /mcp."
        ),
    )
    app.include_router(build_catalog_router(catalog), prefix="/v1")
    app.include_router(build_ingest_router(ingester), prefix="/v1")
    app.include_router(
        build_sessions_router(catalog, sessions, vision, ordering), prefix="/v1"
    )
    app.include_router(build_sim_router(), prefix="/v1")

    mcp = create_mcp_server(catalog, ingester, sessions, vision, ordering)
    app.state.catalog = catalog
    app.state.sessions = sessions
    app.state.mcp = mcp
    app.state.frame_waiters = {}
    mount_mcp(app, mcp)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Legacy pages → unified Alexa+ UI with debug mode
    @app.get("/sim")
    def legacy_sim() -> RedirectResponse:
        return RedirectResponse(url="/?debug=1", status_code=307)

    @app.get("/assembly")
    def legacy_assembly() -> RedirectResponse:
        return RedirectResponse(url="/?debug=1", status_code=307)

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    if DEMO_FRAMES_DIR.is_dir():
        app.mount(
            "/demo-frames",
            StaticFiles(directory=DEMO_FRAMES_DIR),
            name="demo_frames",
        )

    if WEB_DIST.is_dir():
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets),
                name="web_assets",
            )

        @app.get("/")
        def alexa_plus_ui() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

        @app.get("/favicon.svg")
        def favicon() -> FileResponse:
            return FileResponse(WEB_DIST / "favicon.svg")
    elif FRONTEND_DIR.is_dir():

        @app.get("/")
        def assembly_ui_fallback() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
