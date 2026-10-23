from __future__ import annotations

from pydantic import BaseModel, Field


class OrderLineItem(BaseModel):
    partId: str
    name: str = ""
    qty: int = Field(ge=1)
    unitPriceCents: int = Field(ge=0, default=0)
    stockStatus: str | None = Field(
        default=None,
        description="AVAILABLE / OUT_OF_STOCK / UNKNOWN from IKEA APO when reachable",
    )


class ReplacementOrderRequest(BaseModel):
    items: list[OrderLineItem] = Field(min_length=1)
    confirm: bool = Field(
        default=True,
        description=(
            "Hackathon mock always confirms. No email or payment collected — "
            "simulated order confirmation only."
        ),
    )


class ReplacementOrderResult(BaseModel):
    orderId: str
    status: str
    currency: str = "usd"
    totalCents: int
    items: list[OrderLineItem]
    checkoutUrl: str
    message: str
    provider: str = "mock-ikea-apo"
    estimatedDelivery: str | None = None
    sparePartsUrl: str | None = None


class TaskrabbitBookingRequest(BaseModel):
    """Request an in-person Tasker for assembly help (simulated)."""

    task: str = Field(
        default="Furniture assembly",
        description="Short task title, e.g. Furniture assembly / Fix assembly",
    )
    notes: str | None = Field(
        default=None,
        description="What they need help with (stuck on step, wrong hole, etc.)",
    )
    urgency: str = Field(
        default="today",
        description="today | asap | schedule — affects mock arrival window",
    )
    confirm: bool = True


class TaskrabbitBookingResult(BaseModel):
    bookingId: str
    status: str = "confirmed"
    provider: str = "mock-taskrabbit"
    task: str
    productId: str
    taskerName: str
    taskerRating: float = 4.9
    arrivalWindow: str
    currency: str = "usd"
    totalCents: int
    checkoutUrl: str
    message: str
    notes: str | None = None
