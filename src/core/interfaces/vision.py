from __future__ import annotations

from typing import Protocol

from core.models.catalog import Step
from core.models.vision import InventoryCountResult, OrientationResult, PartIdentityResult


class VisionInspector(Protocol):
    def verify_orientation(
        self,
        *,
        frame_bytes: bytes,
        diagram_bytes: bytes,
        step: Step,
        product_id: str,
    ) -> OrientationResult: ...

    def identify_part(
        self,
        *,
        frame_bytes: bytes,
        step: Step,
        product_id: str,
        bom: list | None = None,
    ) -> PartIdentityResult: ...

    def count_parts(
        self,
        *,
        frame_bytes: bytes,
        bom: list,
        product_id: str,
    ) -> InventoryCountResult: ...
