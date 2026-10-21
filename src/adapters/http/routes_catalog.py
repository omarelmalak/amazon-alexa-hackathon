from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from core.models.catalog import HardwarePart, ProductManifest, ProductSummary, Step
from core.services.catalog import (
    FilesystemProductCatalog,
    ProductNotFoundError,
    StepNotFoundError,
)


def build_catalog_router(catalog: FilesystemProductCatalog) -> APIRouter:
    router = APIRouter(tags=["catalog"])

    @router.get("/products", response_model=list[ProductSummary])
    def list_products() -> list[ProductSummary]:
        return catalog.list_products()

    @router.get("/products/{product_id}", response_model=ProductManifest)
    def get_product(product_id: str) -> ProductManifest:
        try:
            return catalog.get_product(product_id)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/products/{product_id}/bom", response_model=list[HardwarePart])
    def get_bom(product_id: str) -> list[HardwarePart]:
        try:
            return catalog.get_bom(product_id)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/products/{product_id}/steps", response_model=list[Step])
    def list_steps(product_id: str) -> list[Step]:
        try:
            return catalog.list_steps(product_id)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/products/{product_id}/steps/{step_number}")
    def get_step(product_id: str, step_number: int) -> dict:
        try:
            step = catalog.get_step(product_id, step_number)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StepNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        payload = step.model_dump()
        payload["productId"] = product_id
        payload["diagramUrl"] = (
            f"/v1/products/{product_id}/steps/{step_number}/diagram"
        )
        return payload

    @router.get("/products/{product_id}/steps/{step_number}/diagram")
    def get_diagram(product_id: str, step_number: int) -> Response:
        try:
            png = catalog.get_diagram(product_id, step_number)
        except ProductNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StepNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=png, media_type="image/png")

    return router
