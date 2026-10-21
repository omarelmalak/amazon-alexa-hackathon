from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.models.ordering import ReplacementOrderRequest, ReplacementOrderResult


class MockStripePartsOrdering:
    """
    Hackathon stand-in for Stripe/Amazon replacement-part checkout.

    Returns a deterministic mock checkout URL. Swap for real Stripe Checkout
    later without changing the HTTP/MCP adapters.
    """

    def create_replacement_order(
        self,
        *,
        product_id: str,
        session_id: str,
        request: ReplacementOrderRequest,
    ) -> ReplacementOrderResult:
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        total = sum(item.unitPriceCents * item.qty for item in request.items)
        lines = ", ".join(f"{i.qty}× {i.partId}" for i in request.items)
        checkout_url = (
            f"https://checkout.stripe.com/c/pay/mock_{order_id}"
            f"?product={product_id}&session={session_id[:8]}"
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return ReplacementOrderResult(
            orderId=order_id,
            status="pending_checkout",
            currency="usd",
            totalCents=total,
            items=list(request.items),
            checkoutUrl=checkout_url,
            message=(
                f"Mock order created at {ts} for {product_id}: {lines}. "
                f"Total ${total / 100:.2f}. Open checkoutUrl to continue (simulated)."
            ),
            provider="mock-stripe",
        )
