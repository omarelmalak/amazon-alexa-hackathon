from __future__ import annotations

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Pixel box relative to the webcam frame (origin top-left)."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=0)
    h: int = Field(ge=0)


class OrientationResult(BaseModel):
    aligned: bool
    errorDescription: str | None = None
    boundingBox: BoundingBox | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    stepCompleted: bool = False
    rawNotes: str | None = None


class InspectFrameRequest(BaseModel):
    """Webcam (or test) frame as base64 — raw or data-URL prefix OK."""

    frameBase64: str = Field(min_length=8)
    stepNumber: int | None = Field(
        default=None,
        ge=1,
        description="Override session currentStep for this check",
    )


class PartIdentityResult(BaseModel):
    partId: str | None = None
    name: str | None = None
    matchesCurrentStep: bool | None = None
    confidence: float | None = None
    notes: str | None = None


class InventoryCountResult(BaseModel):
    counted: list[dict] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)
    notes: str | None = None
