from __future__ import annotations

from pathlib import Path

from core.models.catalog import HardwarePart, ProductManifest, ProductSummary, Step
from infra.parts_library import (
    PartImageNotFoundError,
    get_part_image_bytes,
    list_part_image_ids,
    load_reference_images,
)


class ProductNotFoundError(LookupError):
    pass


class StepNotFoundError(LookupError):
    pass


class PartNotFoundError(LookupError):
    pass


class FilesystemProductCatalog:
    """Loads product manifests from data/<productId>/manifest.json."""

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root.resolve()

    def list_products(self) -> list[ProductSummary]:
        products: list[ProductSummary] = []
        if not self._data_root.exists():
            return products
        for path in sorted(self._data_root.iterdir()):
            if not path.is_dir():
                continue
            manifest_path = path / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = self._load_manifest(path.name)
            products.append(
                ProductSummary(
                    productId=manifest.productId,
                    displayName=manifest.displayName,
                    stepCount=len(manifest.steps),
                    bomItemCount=len(manifest.bom),
                )
            )
        return products

    def get_product(self, product_id: str) -> ProductManifest:
        return self._load_manifest(product_id)

    def get_bom(self, product_id: str) -> list[HardwarePart]:
        return list(self._load_manifest(product_id).bom)

    def list_steps(self, product_id: str) -> list[Step]:
        return list(self._load_manifest(product_id).steps)

    def get_step(self, product_id: str, step_number: int) -> Step:
        for step in self._load_manifest(product_id).steps:
            if step.number == step_number:
                return step
        raise StepNotFoundError(
            f"Step {step_number} not found for product '{product_id}'"
        )

    def get_diagram(self, product_id: str, step_number: int) -> bytes:
        step = self.get_step(product_id, step_number)
        product_dir = self._product_dir(product_id)
        diagram_path = (product_dir / step.diagram).resolve()
        if not str(diagram_path).startswith(str(product_dir.resolve())):
            raise StepNotFoundError(f"Invalid diagram path for step {step_number}")
        if not diagram_path.is_file():
            raise StepNotFoundError(f"Diagram missing for step {step_number}: {step.diagram}")
        return diagram_path.read_bytes()

    def get_bom_diagram(self, product_id: str) -> bytes:
        product_dir = self._product_dir(product_id)
        path = (product_dir / "diagrams" / "bom.png").resolve()
        if not str(path).startswith(str(product_dir.resolve())) or not path.is_file():
            raise ProductNotFoundError(
                f"BOM diagram missing for product '{product_id}' (expected diagrams/bom.png)"
            )
        return path.read_bytes()

    def list_part_images(self, product_id: str) -> list[str]:
        return list_part_image_ids(self._product_dir(product_id))

    def get_part_image(self, product_id: str, part_id: str) -> bytes:
        try:
            return get_part_image_bytes(self._product_dir(product_id), part_id)
        except PartImageNotFoundError as exc:
            raise PartNotFoundError(str(exc)) from exc

    def get_reference_images(
        self, product_id: str, part_ids: list[str] | None = None
    ) -> dict[str, bytes]:
        product_dir = self._product_dir(product_id)
        if part_ids is None:
            part_ids = list_part_image_ids(product_dir)
        return load_reference_images(product_dir, part_ids)

    def _product_dir(self, product_id: str) -> Path:
        path = self._data_root / product_id
        if not path.is_dir() or not (path / "manifest.json").is_file():
            raise ProductNotFoundError(f"Product '{product_id}' not found")
        return path

    def _load_manifest(self, product_id: str) -> ProductManifest:
        product_dir = self._product_dir(product_id)
        return ProductManifest.model_validate_json(
            (product_dir / "manifest.json").read_text(encoding="utf-8")
        )
