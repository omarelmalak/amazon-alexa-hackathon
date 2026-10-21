from __future__ import annotations

from pydantic import BaseModel, Field


class HardwarePart(BaseModel):
    partId: str
    name: str = ""
    qty: int = Field(ge=0)


class Step(BaseModel):
    number: int = Field(ge=1)
    diagram: str
    title: str = ""
    requiredHardware: list[HardwarePart] = Field(default_factory=list)
    completionHints: list[str] = Field(default_factory=list)


class ProductManifest(BaseModel):
    productId: str
    displayName: str
    manualSource: str = "source/manual.pdf"
    bom: list[HardwarePart] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


class ProductSummary(BaseModel):
    productId: str
    displayName: str
    stepCount: int
    bomItemCount: int
