from __future__ import annotations

from pydantic import BaseModel, Field


class OrderLineItem(BaseModel):
    partId: str
    name: str = ""
    qty: int = Field(ge=1)
    unitPriceCents: int = Field(ge=0, default=299)


class ReplacementOrderRequest(BaseModel):
    items: list[OrderLineItem] = Field(min_length=1)
    customerEmail: str | None = None
    confirm: bool = Field(
        default=True,
        description="Hackathon mock always confirms; real Stripe would create a Checkout Session",
    )


class ReplacementOrderResult(BaseModel):
    orderId: str
    status: str
    currency: str = "usd"
    totalCents: int
    items: list[OrderLineItem]
    checkoutUrl: str
    message: str
    provider: str = "mock-stripe"
