"""
IKEA After-Purchase Ordering (APO) spare-parts lookup.

Unofficial read-only API used by IKEA's spare-parts tooling:
  GET https://api.prod.apo.ingka.com/part/{partId}

Checkout itself is not publicly available (US online spare-parts ordering is
often unavailable / store-assisted), so we only use this for name/stock
enrichment. Cloudflare may block automated clients — callers must soft-fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from infra.ikea_fetch import DEFAULT_HEADERS, USER_AGENT

APO_BASE = "https://api.prod.apo.ingka.com"


@dataclass
class IkeaSparePart:
    part_id: str
    name: str = ""
    description: str = ""
    status: str = "UNKNOWN"  # AVAILABLE | OUT_OF_STOCK | UNKNOWN
    stock: int | None = None
    replacement_id: str | None = None
    source: str = "apo"


def lookup_part(
    part_id: str,
    *,
    accept_language: str = "en-US",
    client: httpx.Client | None = None,
    timeout: float = 8.0,
) -> IkeaSparePart | None:
    """Return APO part metadata, or None if unavailable / blocked."""
    pid = str(part_id).strip()
    if not pid:
        return None
    headers = {
        **DEFAULT_HEADERS,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": accept_language,
    }
    owns = client is None
    http = client or httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)
    try:
        resp = http.get(f"{APO_BASE}/part/{pid}")
        if resp.status_code not in (200, 202):
            return None
        # Cloudflare challenge HTML
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" not in ctype and not resp.text.lstrip().startswith(("{", "[")):
            return None
        data: Any = resp.json()
        if not isinstance(data, dict):
            return None
        return IkeaSparePart(
            part_id=str(data.get("itemId") or data.get("replacementId") or pid),
            name=str(data.get("partName") or "").strip(),
            description=str(data.get("partDescription") or "").strip(),
            status=str(data.get("status") or "UNKNOWN").upper(),
            stock=_as_int(data.get("stock")),
            replacement_id=(
                str(data["replacementId"])
                if data.get("replacementId") is not None
                else None
            ),
            source="apo",
        )
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    finally:
        if owns:
            http.close()


def spare_parts_page_url(
    *,
    market: str = "us",
    locale: str = "en",
    part_id: str | None = None,
) -> str:
    """Public IKEA spare-parts landing (real checkout is often store-assisted)."""
    base = f"https://www.ikea.com/{market}/{locale}/customer-service/spare-parts/"
    if part_id:
        return f"{base}?partNumber={part_id}"
    return base


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
