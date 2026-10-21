from __future__ import annotations

from typing import Protocol

from core.models.catalog import HardwarePart, ProductManifest, ProductSummary, Step


class ProductCatalog(Protocol):
    """Read-only product ground truth. Adapters call this — never raw files."""

    def list_products(self) -> list[ProductSummary]: ...

    def get_product(self, product_id: str) -> ProductManifest: ...

    def get_bom(self, product_id: str) -> list[HardwarePart]: ...

    def list_steps(self, product_id: str) -> list[Step]: ...

    def get_step(self, product_id: str, step_number: int) -> Step: ...

    def get_diagram(self, product_id: str, step_number: int) -> bytes: ...
