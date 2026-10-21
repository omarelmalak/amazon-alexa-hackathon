from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.models.assembly import (
    AssemblyMode,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
)
from core.models.vision import InspectFrameRequest, OrientationResult
from core.services.catalog import (
    FilesystemProductCatalog,
    ProductNotFoundError,
    StepNotFoundError,
)
from core.services.session import InMemoryAssemblySession, SessionNotFoundError
from core.services.vision import (
    CohereVisionInspector,
    VisionNotImplementedError,
    decode_frame_base64,
)
from infra.cohere_client import CohereError


def build_sessions_router(
    catalog: FilesystemProductCatalog,
    sessions: InMemoryAssemblySession,
    vision: CohereVisionInspector,
) -> APIRouter:
    router = APIRouter(tags=["sessions"])

    @router.post("/sessions", response_model=SessionResponse)
    def create_session(body: SessionCreateRequest) -> SessionResponse:
        try:
            session_id, state = sessions.create(body.productId)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SessionResponse(sessionId=session_id, state=state)

    @router.get("/sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str) -> SessionResponse:
        try:
            state = sessions.get_state(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SessionResponse(sessionId=session_id, state=state)

    @router.patch("/sessions/{session_id}", response_model=SessionResponse)
    def patch_session(session_id: str, body: SessionUpdateRequest) -> SessionResponse:
        try:
            state = sessions.update(session_id, body)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ProductNotFoundError, StepNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SessionResponse(sessionId=session_id, state=state)

    @router.post("/sessions/{session_id}/advance", response_model=SessionResponse)
    def advance_session(session_id: str) -> SessionResponse:
        try:
            state = sessions.advance(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StepNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SessionResponse(sessionId=session_id, state=state)

    @router.post(
        "/sessions/{session_id}/inspect/orientation",
        response_model=OrientationResult,
    )
    def inspect_orientation(
        session_id: str, body: InspectFrameRequest
    ) -> OrientationResult:
        try:
            state = sessions.get_state(session_id)
            step_number = body.stepNumber or state.currentStep
            step = catalog.get_step(state.productId, step_number)
            diagram = catalog.get_diagram(state.productId, step_number)
            frame = decode_frame_base64(body.frameBase64)
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
            return result
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ProductNotFoundError, StepNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CohereError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/inspect/part")
    def inspect_part(session_id: str, body: InspectFrameRequest) -> dict:
        raise HTTPException(
            status_code=501,
            detail="identify_part not implemented yet (Phase 2 stub)",
        )

    @router.post("/sessions/{session_id}/inspect/inventory")
    def inspect_inventory(session_id: str, body: InspectFrameRequest) -> dict:
        raise HTTPException(
            status_code=501,
            detail="count_parts not implemented yet (Phase 2 stub)",
        )

    # Silence unused for now — stubs will use vision later
    _ = VisionNotImplementedError

    return router
