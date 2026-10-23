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
    # Proof the checker compared webcam vs the real step diagram (not inventing advice).
    diagramStep: int | None = None
    diagramTitle: str | None = None
    diagramBytes: int | None = None
    diagramPreviewBase64: str | None = None
    annotatedFrameBase64: str | None = None
    diagramObserved: str | None = None
    webcamObserved: str | None = None


class InspectFrameRequest(BaseModel):
    """Webcam (or test) frame as base64 — raw or data-URL prefix OK."""

    frameBase64: str = Field(min_length=8)
    stepNumber: int | None = Field(
        default=None,
        ge=1,
        description="Override session currentStep for this check",
    )
    detectPrompt: str | None = Field(
        default=None,
        description=(
            "GroundingDINO open-vocab caption. Use SINGULAR class names only "
            "(e.g. 'hex socket screw . screw . bolt .'), never plurals like 'screws'."
        ),
    )


class PartDetection(BaseModel):
    """One GroundingDINO (or fallback) box on the webcam frame."""

    index: int = 0
    label: str = "screw"
    score: float | None = None
    box: BoundingBox


class PartComparison(BaseModel):
    """Score for one detected crop vs one reference part photo."""

    detectionIndex: int
    partId: str
    partName: str | None = None
    similarity: float = Field(ge=0.0, le=1.0)
    notes: str | None = None


class PartDebugStep(BaseModel):
    """One card in the debug chat trail (detection → crop → compare → winner)."""

    kind: str  # status | annotated | crop | compare | winner
    title: str
    text: str | None = None
    detectionIndex: int | None = None
    partId: str | None = None
    similarity: float | None = None
    imageBase64: str | None = None
    refImageBase64: str | None = None


class PartIdentityResult(BaseModel):
    partId: str | None = None
    name: str | None = None
    matchesCurrentStep: bool | None = None
    confidence: float | None = None
    notes: str | None = None
    detector: str | None = None
    detectPrompt: str | None = None
    targetPartId: str | None = None
    detections: list[PartDetection] = Field(default_factory=list)
    comparisons: list[PartComparison] = Field(default_factory=list)
    winnerDetectionIndex: int | None = None
    annotatedFrameBase64: str | None = None
    referencePartIds: list[str] = Field(default_factory=list)
    debugPipeline: list[str] = Field(
        default_factory=list,
        description="Human-readable steps for the debug transcript",
    )
    debugSteps: list[PartDebugStep] = Field(
        default_factory=list,
        description="Per-crop debug cards for the Alexa+ debug chat",
    )


class PartCount(BaseModel):
    partId: str
    name: str = ""
    expectedQty: int = 0
    seenQty: int = 0
    evidence: str | None = None


class InventoryCountResult(BaseModel):
    """Pure inspection result — does not create orders."""

    counted: list[PartCount] = Field(default_factory=list)
    missing: list[PartCount] = Field(default_factory=list)
    extras: list[PartCount] = Field(default_factory=list)
    notes: str | None = None
    detector: str | None = None
    detectPrompt: str | None = None
    annotatedFrameBase64: str | None = None
    debugSteps: list[PartDebugStep] = Field(default_factory=list)
