from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.models.ordering import (
    OrderLineItem,
    ReplacementOrderRequest,
    ReplacementOrderResult,
    TaskrabbitBookingRequest,
    TaskrabbitBookingResult,
)
from infra.ikea_apo import lookup_part, spare_parts_page_url

# Small fasteners are often free/cheap at IKEA; use demo prices when omitted.
_DEFAULT_UNIT_CENTS = 99
_TASKRABBIT_TASKERS = (
    ("Jordan Lee", 4.98),
    ("Sam Rivera", 4.95),
    ("Alex Chen", 4.92),
    ("Morgan Patel", 4.97),
)
_TASKRABBIT_BASE = "https://www.taskrabbit.com/services/furniture-assembly"


class MockIkeaSparePartsOrdering:
    """
    Simulate IKEA spare-parts checkout after inventory finds missing hardware.

    Real IKEA online spare-parts ordering is limited / often store-assisted, and
    the unofficial APO API is read-only (lookup only) and frequently Cloudflare-
    gated. This mock:
      1) optionally enriches line items via APO when reachable
      2) creates a confirmed mock order + checkout deep-link for the demo
    """

    def __init__(self, *, market: str = "us", locale: str = "en") -> None:
        self._market = market
        self._locale = locale

    def create_replacement_order(
        self,
        *,
        product_id: str,
        session_id: str,
        request: ReplacementOrderRequest,
        bom_names: dict[str, str] | None = None,
    ) -> ReplacementOrderResult:
        names = dict(bom_names or {})
        enriched: list[OrderLineItem] = [
            self._enrich_line(item, bom_names=names) for item in request.items
        ]
        order_id = f"IKEA-SP-{uuid.uuid4().hex[:10].upper()}"
        total = sum(item.unitPriceCents * item.qty for item in enriched)
        lines = ", ".join(
            f"{i.qty}× {i.name or 'part'} (#{i.partId})" for i in enriched
        )
        # Deep-link the first part; landing page covers the rest.
        primary = enriched[0].partId if enriched else None
        spare_url = spare_parts_page_url(
            market=self._market, locale=self._locale, part_id=primary
        )
        # Demo "checkout" stays on our mock path so the URL always works in talk-track.
        checkout_url = (
            f"{spare_url}"
            f"{'&' if '?' in spare_url else '?'}"
            f"mockOrder={order_id}&product={product_id}&session={session_id[:8]}"
        )
        eta = (datetime.now(timezone.utc) + timedelta(days=5)).strftime(
            "%a %b %d"
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        free = total == 0
        total_txt = "free (IKEA small spare parts)" if free else f"${total / 100:.2f}"
        return ReplacementOrderResult(
            orderId=order_id,
            status="confirmed",
            currency="usd",
            totalCents=total,
            items=enriched,
            checkoutUrl=checkout_url,
            sparePartsUrl=spare_url,
            estimatedDelivery=eta,
            message=(
                f"IKEA spare-parts order {order_id} placed at {ts} for "
                f"{product_id}: {lines}. Total {total_txt}. "
                f"Estimated delivery {eta}. "
                f"(Simulated checkout — no real charge. Open checkoutUrl for the "
                f"IKEA spare-parts page.)"
            ),
            provider="mock-ikea-apo",
        )

    def _enrich_line(
        self,
        item: OrderLineItem,
        *,
        bom_names: dict[str, str] | None = None,
    ) -> OrderLineItem:
        # Prefer the kit/BOM name (e.g. "hex socket screw") over APO jargon.
        bom_name = (bom_names or {}).get(item.partId, "").strip()
        name = (item.name or "").strip() or bom_name
        # Drop useless placeholders the agent sometimes sends.
        if name.lower() in {"", "part", "hardware", "item", item.partId.lower()}:
            name = bom_name
        status = item.stockStatus
        price = item.unitPriceCents
        part_id = item.partId
        apo = lookup_part(item.partId, accept_language=f"en-{self._market.upper()}")
        if apo:
            status = apo.status or status
            # Keep original partId for the receipt; APO name only if we still lack one.
            if not name and apo.name:
                name = apo.name.title() if apo.name.isupper() else apo.name
        else:
            status = status or "UNKNOWN"
        if price <= 0:
            price = _DEFAULT_UNIT_CENTS
        # Friendly fallback from partId families used in this hackathon kit.
        if not name:
            name = _friendly_part_name(part_id)
        return OrderLineItem(
            partId=part_id,
            name=name,
            qty=item.qty,
            unitPriceCents=price,
            stockStatus=status,
        )

    def book_taskrabbit_help(
        self,
        *,
        product_id: str,
        session_id: str,
        request: TaskrabbitBookingRequest,
        step_number: int | None = None,
    ) -> TaskrabbitBookingResult:
        """Simulate booking a Taskrabbit Tasker for in-person assembly help."""
        booking_id = f"TR-{uuid.uuid4().hex[:10].upper()}"
        # Stable-ish pick from session id so demos don't reshuffle every retry.
        idx = int(session_id[:8], 16) % len(_TASKRABBIT_TASKERS)
        tasker_name, rating = _TASKRABBIT_TASKERS[idx]
        now = datetime.now(timezone.utc)
        urgency = (request.urgency or "today").strip().lower()
        if urgency in ("asap", "now", "urgent"):
            window_start = now + timedelta(minutes=45)
            window_end = window_start + timedelta(hours=1)
            total = 8900
        elif urgency in ("schedule", "later", "tomorrow"):
            window_start = now + timedelta(days=1, hours=2)
            window_end = window_start + timedelta(hours=2)
            total = 6900
        else:
            window_start = now + timedelta(hours=3)
            window_end = window_start + timedelta(hours=2)
            total = 7900
        def _fmt(dt: datetime, *, with_day: bool = False) -> str:
            hour = dt.strftime("%I").lstrip("0") or "12"
            base = f"{hour}:{dt.strftime('%M %p')}"
            return f"{dt.strftime('%a')} {base}" if with_day else base

        arrival = f"{_fmt(window_start, with_day=True)} – {_fmt(window_end)}"
        task = (request.task or "Furniture assembly").strip() or "Furniture assembly"
        step_bit = f" (step {step_number})" if step_number else ""
        notes = (request.notes or "").strip() or None
        checkout = (
            f"{_TASKRABBIT_BASE}"
            f"?mockBooking={booking_id}&product={product_id}"
            f"&session={session_id[:8]}"
        )
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        note_txt = f" Notes: {notes}." if notes else ""
        return TaskrabbitBookingResult(
            bookingId=booking_id,
            status="confirmed",
            provider="mock-taskrabbit",
            task=f"{task} — {product_id.upper()}{step_bit}",
            productId=product_id,
            taskerName=tasker_name,
            taskerRating=rating,
            arrivalWindow=arrival,
            currency="usd",
            totalCents=total,
            checkoutUrl=checkout,
            notes=notes,
            message=(
                f"Taskrabbit booking {booking_id} confirmed at {ts}: "
                f"{tasker_name} ({rating}★) for {product_id.upper()}{step_bit}. "
                f"Arrival window {arrival}. Est. ${total / 100:.2f}.{note_txt} "
                f"(Simulated — no real Taskrabbit charge.)"
            ),
        )


def _friendly_part_name(part_id: str) -> str:
    """Last-resort label when BOM/APO names are missing."""
    known = {
        "130915": "hex socket screw",
        "100092": "allen key",
        "112357": "plastic push plug",
    }
    return known.get(part_id, f"IKEA part {part_id}")


# Back-compat alias used by older imports / docs.
MockStripePartsOrdering = MockIkeaSparePartsOrdering
