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
from core.models.ordering import (
    OrderLineItem,
    ReplacementOrderRequest,
    TaskrabbitBookingRequest,
)
from core.services.catalog import (
    FilesystemProductCatalog,
    PartNotFoundError,
    ProductNotFoundError,
    StepNotFoundError,
)
from infra.parts_library import bytes_to_data_url
from core.services.ingest import (
    IkeaLookupError,
    IkeaManualIngester,
    InvalidProductQueryError,
    ProductExistsError,
)
from core.services.ordering import MockIkeaSparePartsOrdering
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
    ordering: MockIkeaSparePartsOrdering | None = None,
) -> MCPServer:
    """Register MCP tools that call the same core interfaces as ``adapters/http``."""
    ordering = ordering or MockIkeaSparePartsOrdering()
    mcp = MCPServer(
        name="ikea-flatpack-copilot",
        title="IKEA Flatpack Co-Pilot",
        instructions=(
            "Help the user assemble IKEA furniture. Prefer catalog/session tools "
            "before vision. Inspect tools need a webcam frameBase64 from the Fire TV "
            "or browser UI. Never scrape ikea.com — call ingest_product instead. "
            "Inventory inspect never places an order; use order_replacement_parts "
            "only after the user confirms missing items. For in-person help "
            "(Taskrabbit / handyman), use book_taskrabbit_help after they confirm."
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
            "Find, download, and process an IKEA assembly manual. "
            "Call ONLY after the user names a specific product/series "
            "(e.g. query='KYRRE' or 'RISATORP'). Do NOT call for vague phrases "
            "like 'I want to build something'. Backend owns the IKEA fetch."
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
        except InvalidProductQueryError as exc:
            return {
                "error": True,
                "needProductName": True,
                "message": str(exc),
            }
        except ProductExistsError as exc:
            # Return structured info so an agent can continue without force=true.
            import re as _re

            m = _re.search(r"Product '([^']+)' already exists", str(exc))
            resolved = m.group(1) if m else (product_id or "").lower()
            try:
                existing = catalog.get_product(resolved)
                return {
                    "alreadyExists": True,
                    "productId": existing.productId,
                    "displayName": existing.displayName,
                    "stepCount": len(existing.steps),
                    "bomItemCount": len(existing.bom),
                    "message": str(exc),
                }
            except Exception:
                raise ValueError(str(exc)) from exc
        except IkeaLookupError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Start or resume an assembly session for a productId. "
            "If a session for that product already exists, returns it "
            "(does not reset progress). Pass the catalog productId "
            "(e.g. 'kyrre'), never invent a session id."
        )
    )
    def create_session(product_id: str) -> dict[str, Any]:
        try:
            session_id, state, reused = sessions.create(product_id, reuse=True)
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "sessionId": session_id,
            "state": _dump(state),
            "reused": reused,
        }

    @mcp.tool(
        description=(
            "Get current AssemblyState. Pass the session UUID from create_session "
            "(not the product name)."
        )
    )
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            resolved = sessions.resolve_session_id(session_id)
            state = sessions.get_state(resolved)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        return {"sessionId": resolved, "state": _dump(state)}

    @mcp.tool(
        description=(
            "Advance the session to the next assembly step. "
            "Pass the session UUID from create_session."
        )
    )
    def advance_session(session_id: str) -> dict[str, Any]:
        try:
            resolved = sessions.resolve_session_id(session_id)
            state = sessions.advance(resolved)
        except (SessionNotFoundError, StepNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return {"sessionId": resolved, "state": _dump(state)}

    @mcp.tool(
        description=(
            "Verify webcam frame against the official step diagram loaded from "
            "data/<product>/diagrams (Image A=webcam, Image B=diagram). "
            "Returns aligned/stepCompleted plus diagramObserved/webcamObserved so "
            "you can explain mismatches from the real drawing — do not invent "
            "screw directions. Same as POST /v1/sessions/.../inspect/orientation."
        )
    )
    def inspect_orientation(
        session_id: str,
        frame_base64: str,
        step_number: int | None = None,
    ) -> dict[str, Any]:
        try:
            sid = sessions.resolve_session_id(session_id)
            state = sessions.get_state(sid)
            step_n = step_number or state.currentStep
            step = catalog.get_step(state.productId, step_n)
            diagram = catalog.get_diagram(state.productId, step_n)
            frame = decode_frame_base64(frame_base64)
            sessions.update(
                sid,
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
                    sid,
                    SessionUpdateRequest(
                        stepCompleted=True,
                        activeMode=AssemblyMode.IDLE_MONITORING,
                    ),
                )
            else:
                sessions.update(
                    sid,
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
            "Fetch the local reference photo for a hardware partId from "
            "data/<product>/parts/{partId}.png (populated at ingest from the BOM, "
            "or manually uploaded). Use before/with inspect_part so you know what "
            "the exact screw/tool looks like. Returns imageBase64 + imageUrl."
        )
    )
    def get_part_image(product_id: str, part_id: str) -> dict[str, Any]:
        try:
            png = catalog.get_part_image(product_id, part_id)
            bom = {h.partId: h for h in catalog.get_bom(product_id)}
            part = bom.get(part_id)
            return {
                "productId": product_id,
                "partId": part_id,
                "name": part.name if part else None,
                "imageUrl": f"/v1/products/{product_id}/parts/{part_id}",
                "imageBase64": bytes_to_data_url(png),
                "availablePartIds": catalog.list_part_images(product_id),
            }
        except (ProductNotFoundError, PartNotFoundError) as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Identify which hardware part is in the webcam frame for the current step. "
            "Runs GroundingDINO with detect_prompt, crops each box, compares every crop "
            "to the step's correct part photo under parts/{partId}.png, and returns the "
            "highest-similarity match plus debugSteps and annotatedFrameBase64.\n"
            "detect_prompt (critical): open-vocab classes separated by ' . '. "
            "ALWAYS use SINGULAR nouns only — e.g. "
            "'hex socket screw . screw . bolt . allen key .' "
            "NEVER plurals ('screws', 'bolts', 'fasteners') — plurals make DINO draw "
            "one box around multiple parts. Tailor classes to what the user is showing "
            "(screws vs allen key vs plastic plug). Omit detect_prompt to use the default "
            "singular screw/bolt prompt. Same as POST /v1/sessions/.../inspect/part."
        )
    )
    def inspect_part(
        session_id: str,
        frame_base64: str,
        step_number: int | None = None,
        detect_prompt: str | None = None,
    ) -> dict[str, Any]:
        try:
            sid = sessions.resolve_session_id(session_id)
            state = sessions.get_state(sid)
            step = catalog.get_step(
                state.productId, step_number or state.currentStep
            )
            bom = catalog.get_bom(state.productId)
            frame = decode_frame_base64(frame_base64)
            ref_ids = [h.partId for h in (step.requiredHardware or bom)]
            refs = catalog.get_reference_images(state.productId, ref_ids)
            if not refs:
                refs = catalog.get_reference_images(state.productId)
            sessions.update(
                sid,
                SessionUpdateRequest(activeMode=AssemblyMode.INSPECTING_PART),
            )
            result = vision.identify_part(
                frame_bytes=frame,
                step=step,
                product_id=state.productId,
                bom=bom,
                reference_images=refs,
                detect_prompt=detect_prompt,
            )
            sessions.update(
                sid,
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
            "Count kit hardware in a webcam photo vs the product BOM. "
            "Uses Cohere only: rejects non-parts scenes (faces, empty desks), "
            "then counts ONLY manifest partIds. Returns counted/missing — "
            "never places an order. Same as POST "
            "/v1/sessions/.../inspect/inventory."
        )
    )
    def inspect_inventory(
        session_id: str,
        frame_base64: str,
        detect_prompt: str | None = None,
    ) -> dict[str, Any]:
        try:
            sid = sessions.resolve_session_id(session_id)
            state = sessions.get_state(sid)
            bom = catalog.get_bom(state.productId)
            frame = decode_frame_base64(frame_base64)
            refs = catalog.get_reference_images(
                state.productId, [h.partId for h in bom]
            )
            sessions.update(
                sid,
                SessionUpdateRequest(activeMode=AssemblyMode.COUNTING),
            )
            result = vision.count_parts(
                frame_bytes=frame,
                bom=bom,
                product_id=state.productId,
                reference_images=refs,
                detect_prompt=detect_prompt,
            )
            sessions.update(
                sid,
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
            "Place a simulated IKEA spare-parts order for missing hardware. "
            "Pass items from inspect_inventory missing[] with partId, the real "
            "BOM name (e.g. 'hex socket screw'), and qty. Do NOT ask for email "
            "or payment. Returns a mock confirmed order + checkoutUrl "
            "(no real charge). Same as POST …/orders/replacement-parts."
        )
    )
    def order_replacement_parts(
        session_id: str,
        items: list[dict[str, Any]],
        confirm: bool = True,
    ) -> dict[str, Any]:
        """No email/payment — simulated IKEA spare-parts confirmation only."""
        try:
            sid = sessions.resolve_session_id(session_id)
            state = sessions.get_state(sid)
            bom_names = {
                h.partId: (h.name or "")
                for h in catalog.get_bom(state.productId)
            }
            # Fill blank names from the product BOM before checkout.
            line_items: list[OrderLineItem] = []
            for raw in items:
                line = OrderLineItem.model_validate(raw)
                if not (line.name or "").strip():
                    line = line.model_copy(
                        update={"name": bom_names.get(line.partId, "")}
                    )
                line_items.append(line)
            request = ReplacementOrderRequest(items=line_items, confirm=confirm)
            result = ordering.create_replacement_order(
                product_id=state.productId,
                session_id=sid,
                request=request,
                bom_names=bom_names,
            )
            return _dump(result)
        except SessionNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except ProductNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool(
        description=(
            "Book a simulated Taskrabbit Tasker for in-person furniture assembly "
            "help when the user is stuck or asks for someone to come over "
            "(Taskrabbit, handyman, 'send someone'). Pass optional notes about "
            "what they need help with and urgency (today|asap|schedule). "
            "Wait for a clear yes before calling. Returns mock bookingId, "
            "tasker, arrival window, and checkoutUrl — no real charge. "
            "Same as POST …/orders/taskrabbit."
        )
    )
    def book_taskrabbit_help(
        session_id: str,
        task: str = "Furniture assembly",
        notes: str | None = None,
        urgency: str = "today",
        confirm: bool = True,
    ) -> dict[str, Any]:
        try:
            sid = sessions.resolve_session_id(session_id)
            state = sessions.get_state(sid)
            request = TaskrabbitBookingRequest(
                task=task,
                notes=notes,
                urgency=urgency,
                confirm=confirm,
            )
            result = ordering.book_taskrabbit_help(
                product_id=state.productId,
                session_id=sid,
                request=request,
                step_number=state.currentStep,
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
