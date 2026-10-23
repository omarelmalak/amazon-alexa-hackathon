from __future__ import annotations

from typing import Protocol

from core.models.ordering import (
    ReplacementOrderRequest,
    ReplacementOrderResult,
    TaskrabbitBookingRequest,
    TaskrabbitBookingResult,
)


class PartsOrdering(Protocol):
    def create_replacement_order(
        self,
        *,
        product_id: str,
        session_id: str,
        request: ReplacementOrderRequest,
        bom_names: dict[str, str] | None = None,
    ) -> ReplacementOrderResult: ...

    def book_taskrabbit_help(
        self,
        *,
        product_id: str,
        session_id: str,
        request: TaskrabbitBookingRequest,
        step_number: int | None = None,
    ) -> TaskrabbitBookingResult: ...
