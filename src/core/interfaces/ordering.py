from __future__ import annotations

from typing import Protocol

from core.models.ordering import ReplacementOrderRequest, ReplacementOrderResult


class PartsOrdering(Protocol):
    def create_replacement_order(
        self,
        *,
        product_id: str,
        session_id: str,
        request: ReplacementOrderRequest,
    ) -> ReplacementOrderResult: ...
