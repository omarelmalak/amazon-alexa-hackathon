"""Streamable HTTP MCP tools — thin wrappers over the same core services as FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server import MCPServer
from pydantic import BaseModel

from core.models.assembly import AssemblyMode, SessionUpdateRequest
from core.models.ingest import IngestRequest
from core.models.ordering import OrderLineItem, ReplacementOrderRequest
from core.services.catalog import (
    FilesystemProductCatalog,
    ProductNotFoundError,
    StepNotFoundError,
)
from core.services.ingest import IkeaLookupError, IkeaManualIngester, ProductExistsError
from core.services.ordering import MockStripePartsOrdering
from core.services.session import InMemoryAssemblySession, SessionNotFoundError
from core.services.vision import CohereVisionInspector, decode_frame_base64
from infra.cohere_client import CohereError


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def create_mcp_server(
    catalog: FilesystemProductCatalog,
    ingester: IkeaManualIngester,
    sessions: InMemoryAssemblySession,
    vision: CohereVisionInspector,
    ordering: MockStripePartsOrdering | None = None,
) -> MCPServer:
    """Register MCP tools that call the same core interfaces as ``adapters/http``."""
    ordering = ordering or MockStripePartsOrdering()
    mcp = MCPServer(
        name="ikea-flatpack-copilot",
        title="IKEA Flatpack Co-Pilot",
        instructions=(
            "Help the user assemble IKEA furniture. Prefer catalog/session tools "
            "before vision. Inspect tools need a webcam frameBase64 from the Fire TV "
            "or browser UI. Never scrape ikea.com — call ingest_product instead. "
            "Inventory inspect never places an order; use order_replacement_parts "
            "only after the user confirms missing items."
        ),
    )

    @mcp.tool(description="List products with local ground-truth packages.")
    def list_products() -> list[dict[str, Any]]:
        return [_dump(p) for p in catalog.list_products()]

    @mcp.tool(description="Get product manifest (display name, steps, BOM summary).")
    def get_product(product_id: str) -> dict[str, Any]:
        try:
            return _dump(catalog.get_product(product_id))
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(description="List bill-of-materials hardware parts for a product.")
    def list_bom(product_id: str) -> list[dict[str, Any]]:
        try:
            return [_dump(p) for p in catalog.get_bom(product_id)]
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(description="List assembly steps (metadata only, no diagram bytes).")
    def list_assembly_steps(product_id: str) -> list[dict[str, Any]]:
        try:
            return [_dump(s) for s in catalog.list_steps(product_id)]
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Get one assembly step plus required hardware and completionHints. "
            "Diagram bytes are not returned — use the browser/Fire TV UI diagram URL."
        )
    )
    def get_assembly_step(product_id: str, step_number: int) -> dict[str, Any]:
        try:
            step = catalog.get_step(product_id, step_number)
        except (ProductNotFoundError, StepNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        payload = _dump(step)
        payload["productId"] = product_id
        payload["diagramUrl"] = (
            f"/v1/products/{product_id}/steps/{step_number}/diagram"
        )
        return payload

    @mcp.tool(
        description=(
            "Find, download, and process an IKEA assembly manual by spoken product "
            "name (e.g. KYRRE). Backend owns the IKEA fetch — do not scrape yourself."
        )
    )
    def ingest_product(
        query: str,
        market: str = "us",
        locale: str = "en",
        product_id: str | None = None,
        force: bool = False,
        enrich: bool = True,
    ) -> dict[str, Any]:
        body = IngestRequest(
            query=query,
            market=market,
            locale=locale,
            product_id=product_id,
            force=force,
            enrich=enrich,
        )
        try:
            return _dump(ingester.ingest(body))
        except ProductExistsError as exc:
            raise ValueError(str(exc)) from exc
        except IkeaLookupError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(description="Start an assembly session for a productId.")
    def create_session(product_id: str) -> dict[str, Any]:
        try:
            session_id, state = sessions.create(product_id)
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return {"sessionId": session_id, "state": _dump(state)}

    @mcp.tool(description="Get current AssemblyState for a session.")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            state = sessions.get_state(session_id)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return {"sessionId": session_id, "state": _dump(state)}

    @mcp.tool(description="Advance the session to the next assembly step.")
    def advance_session(session_id: str) -> dict[str, Any]:
        try:
            state = sessions.advance(session_id)
        except (SessionNotFoundError, StepNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return {"sessionId": session_id, "state": _dump(state)}

    @mcp.tool(
        description=(
            "Verify webcam frame orientation against the current (or given) step "
            "diagram. Same path as POST /v1/sessions/.../inspect/orientation. "
            "Pass frameBase64 from the Fire TV / browser UI."
        )
    )
    def inspect_orientation(
        session_id: str,
        frame_base64: str,
        step_number: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = sessions.get_state(session_id)
            step_n = step_number or state.currentStep
            step = catalog.get_step(state.productId, step_n)
            diagram = catalog.get_diagram(state.productId, step_n)
            frame = decode_frame_base64(frame_base64)
            sessions.update(
                session_id,
                SessionUpdateRequest(activeMode=AssemblyMode.INSPECTING_ORIENTATION),
            )
            result = vision.verify_orientation(
                frame_bytes=frame,
                diagram_bytes=diagram,
                step=step,
                product_id=state.productId,
            )
            if result.stepCompleted and result.aligned:
                sessions.update(
                    session_id,
                    SessionUpdateRequest(
                        stepCompleted=True,
                        activeMode=AssemblyMode.IDLE_MONITORING,
                    ),
                )
            else:
                sessions.update(
                    session_id,
                    SessionUpdateRequest(activeMode=AssemblyMode.IDLE_MONITORING),
                )
            return _dump(result)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except (ProductNotFoundError, StepNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        except CohereError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Identify which hardware part is in the webcam frame for the current step. "
            "Same path as POST /v1/sessions/.../inspect/part."
        )
    )
    def inspect_part(
        session_id: str,
        frame_base64: str,
        step_number: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = sessions.get_state(session_id)
            step = catalog.get_step(
                state.productId, step_number or state.currentStep
            )
            bom = catalog.get_bom(state.productId)
            frame = decode_frame_base64(frame_base64)
            sessions.update(
                session_id,
                SessionUpdateRequest(activeMode=AssemblyMode.INSPECTING_PART),
            )
            result = vision.identify_part(
                frame_bytes=frame,
                step=step,
                product_id=state.productId,
                bom=bom,
            )
            sessions.update(
                session_id,
                SessionUpdateRequest(activeMode=AssemblyMode.IDLE_MONITORING),
            )
            return _dump(result)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except (ProductNotFoundError, StepNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        except CohereError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Count hardware in a webcam frame vs BOM. Returns counted/missing only — "
            "never places an order. Same as POST /v1/sessions/.../inspect/inventory."
        )
    )
    def inspect_inventory(session_id: str, frame_base64: str) -> dict[str, Any]:
        try:
            state = sessions.get_state(session_id)
            bom = catalog.get_bom(state.productId)
            frame = decode_frame_base64(frame_base64)
            sessions.update(
                session_id,
                SessionUpdateRequest(activeMode=AssemblyMode.COUNTING),
            )
            result = vision.count_parts(
                frame_bytes=frame,
                bom=bom,
                product_id=state.productId,
            )
            sessions.update(
                session_id,
                SessionUpdateRequest(activeMode=AssemblyMode.IDLE_MONITORING),
            )
            return _dump(result)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except CohereError as exc:
            raise RuntimeError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Create a mock replacement-parts checkout. Pass explicit line items "
            "(e.g. from a prior inspect_inventory missing[]). Does not re-run vision."
        )
    )
    def order_replacement_parts(
        session_id: str,
        items: list[dict[str, Any]],
        customer_email: str | None = None,
        confirm: bool = True,
    ) -> dict[str, Any]:
        try:
            state = sessions.get_state(session_id)
            request = ReplacementOrderRequest(
                items=[OrderLineItem.model_validate(i) for i in items],
                customerEmail=customer_email,
                confirm=confirm,
            )
            result = ordering.create_replacement_order(
                product_id=state.productId,
                session_id=session_id,
                request=request,
            )
            return _dump(result)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    return mcp


def mount_mcp(app: FastAPI, mcp: MCPServer) -> None:
    """Mount Streamable HTTP MCP at ``/mcp`` and wire session-manager lifespan.

    Call after tools are registered. The parent FastAPI lifespan must enter
    ``mcp.session_manager.run()`` because mounted sub-app lifespans do not run.
    """
    mcp_asgi = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Allow ngrok / LAN Host headers (localhost DNS-rebinding allowlist off).
        host="0.0.0.0",
    )

    existing = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if existing is not None:
            async with existing(_app):
                async with mcp.session_manager.run():
                    yield
        else:
            async with mcp.session_manager.run():
                yield

    app.router.lifespan_context = lifespan
    app.mount("/mcp", mcp_asgi)
