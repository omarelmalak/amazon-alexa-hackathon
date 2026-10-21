from __future__ import annotations

from typing import Protocol

from core.models.assembly import AssemblyState, SessionUpdateRequest


class AssemblySession(Protocol):
    def create(self, product_id: str) -> tuple[str, AssemblyState]: ...

    def get_state(self, session_id: str) -> AssemblyState: ...

    def update(self, session_id: str, patch: SessionUpdateRequest) -> AssemblyState: ...

    def advance(self, session_id: str) -> AssemblyState: ...
