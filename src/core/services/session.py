from __future__ import annotations

import uuid
from threading import Lock

from core.models.assembly import AssemblyMode, AssemblyState, SessionUpdateRequest
from core.services.catalog import (
    FilesystemProductCatalog,
    ProductNotFoundError,
    StepNotFoundError,
)


class SessionNotFoundError(LookupError):
    pass


class InMemoryAssemblySession:
    def __init__(self, catalog: FilesystemProductCatalog) -> None:
        self._catalog = catalog
        self._sessions: dict[str, AssemblyState] = {}
        self._lock = Lock()

    def create(self, product_id: str) -> tuple[str, AssemblyState]:
        product = self._catalog.get_product(product_id)
        if not product.steps:
            raise ProductNotFoundError(f"Product '{product_id}' has no assembly steps")
        first = product.steps[0]
        state = AssemblyState(
            productId=product.productId,
            currentStep=first.number,
            requiredHardware=list(first.requiredHardware),
            stepCompleted=False,
            activeMode=AssemblyMode.IDLE_MONITORING,
        )
        session_id = uuid.uuid4().hex
        with self._lock:
            self._sessions[session_id] = state
        return session_id, state

    def get_state(self, session_id: str) -> AssemblyState:
        with self._lock:
            state = self._sessions.get(session_id)
        if state is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found")
        return state.model_copy(deep=True)

    def update(self, session_id: str, patch: SessionUpdateRequest) -> AssemblyState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise SessionNotFoundError(f"Session '{session_id}' not found")

            data = state.model_dump()
            if patch.currentStep is not None:
                step = self._catalog.get_step(state.productId, patch.currentStep)
                data["currentStep"] = step.number
                data["requiredHardware"] = [h.model_dump() for h in step.requiredHardware]
                data["stepCompleted"] = False
            if patch.stepCompleted is not None:
                data["stepCompleted"] = patch.stepCompleted
            if patch.activeMode is not None:
                data["activeMode"] = patch.activeMode
            updated = AssemblyState.model_validate(data)
            self._sessions[session_id] = updated
            return updated.model_copy(deep=True)

    def advance(self, session_id: str) -> AssemblyState:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise SessionNotFoundError(f"Session '{session_id}' not found")
            steps = self._catalog.list_steps(state.productId)
            numbers = [s.number for s in steps]
            try:
                idx = numbers.index(state.currentStep)
            except ValueError as exc:
                raise StepNotFoundError(
                    f"Current step {state.currentStep} missing from catalog"
                ) from exc
            if idx + 1 >= len(numbers):
                updated = state.model_copy(
                    update={
                        "stepCompleted": True,
                        "activeMode": AssemblyMode.IDLE_MONITORING,
                    }
                )
                self._sessions[session_id] = updated
                return updated.model_copy(deep=True)

            nxt = steps[idx + 1]
            updated = AssemblyState(
                productId=state.productId,
                currentStep=nxt.number,
                requiredHardware=list(nxt.requiredHardware),
                stepCompleted=False,
                activeMode=AssemblyMode.IDLE_MONITORING,
            )
            self._sessions[session_id] = updated
            return updated.model_copy(deep=True)
