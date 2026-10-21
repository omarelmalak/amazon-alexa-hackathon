from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

USER_AGENT = (
    "Mozilla/5.0 (compatible; FlatpackCoPilot/0.1; "
    "+https://github.com/omarelmalak/amazon-alexa-hackathon)"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json,text/html,*/*",
}


@dataclass
class IkeaProductHit:
    name: str
    item_no: str
    product_url: str
    type_name: str = ""


@dataclass
class IkeaManualRef:
    product: IkeaProductHit
    pdf_url: str
    document_id: str | None = None
    label: str | None = None


class IkeaLookupError(RuntimeError):
    pass


def search_products(
    query: str,
    *,
    market: str = "us",
    locale: str = "en",
    limit: int = 8,
    client: httpx.Client | None = None,
) -> list[IkeaProductHit]:
    """Search IKEA catalog via the public search API."""
    url = (
        f"https://sik.search.blue.cdtapps.com/{market}/{locale}/search-result-page"
        f"?types=PRODUCT&q={quote(query)}"
    )
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, timeout=45.0, headers=DEFAULT_HEADERS
    )
    try:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            client.close()

    products = (
        payload.get("searchResultPage", {})
        .get("products", {})
        .get("main", {})
        .get("items", [])
    )
    if not products:
        products = _fallback_product_items(payload)

    hits: list[IkeaProductHit] = []
    for item in products:
        product = item.get("product") or item
        name = (product.get("name") or "").strip()
        item_no = str(
            product.get("itemNoGlobal")
            or product.get("id")
            or product.get("itemNo")
            or ""
        ).strip()
        type_name = (product.get("typeName") or product.get("type") or "").strip()
        product_url = (product.get("pipUrl") or product.get("url") or "").strip()
        if product_url and product_url.startswith("/"):
            product_url = f"https://www.ikea.com{product_url}"
        if not name or not item_no or not product_url:
            continue
        hits.append(
            IkeaProductHit(
                name=name,
                item_no=item_no,
                product_url=product_url,
                type_name=type_name,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def _fallback_product_items(payload: dict) -> list[dict]:
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "product" in node and isinstance(node["product"], dict):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def pick_best_hit(query: str, hits: list[IkeaProductHit]) -> IkeaProductHit:
    if not hits:
        raise IkeaLookupError(f"No IKEA products found for '{query}'")

    needle = query.strip().lower()
    exact = [h for h in hits if h.name.lower() == needle]
    if exact:
        return exact[0]
    prefixed = [h for h in hits if h.name.lower().startswith(needle)]
    if prefixed:
        return prefixed[0]
    contains = [
        h
        for h in hits
        if needle in h.name.lower() or needle in h.type_name.lower()
    ]
    if contains:
        return contains[0]
    return hits[0]


def resolve_assembly_pdf(
    product_url: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[str, str | None, str | None]:
    """Parse the product page for the assembly-instructions PDF URL."""
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, timeout=45.0, headers=DEFAULT_HEADERS
    )
    try:
        response = client.get(product_url)
        response.raise_for_status()
        html = response.text
    finally:
        if owns_client:
            client.close()

    assembly_block = re.search(
        r'"assembly"\s*:\s*\{.*?"attachments"\s*:\s*\[(.*?)\]',
        html,
        flags=re.S,
    )
    if assembly_block:
        urls = re.findall(
            r'"url"\s*:\s*"(https://www\.ikea\.com/[^"]+?\.pdf)"',
            assembly_block.group(1),
        )
        labels = re.findall(r'"label"\s*:\s*"([^"]+)"', assembly_block.group(1))
        if urls:
            pdf_url = urls[0].encode("utf-8").decode("unicode_escape")
            label = labels[0] if labels else None
            doc_id = None
            m = re.search(r"(AA-\d+-\d+)", pdf_url, flags=re.I)
            if m:
                doc_id = m.group(1).upper()
            return pdf_url, doc_id, label

    loose = re.findall(
        r"https://www\.ikea\.com/[a-z]{2}/[a-z]{2}/assembly_instructions/[^\"'\\]+\.pdf",
        html,
        flags=re.I,
    )
    if loose:
        pdf_url = loose[0]
        m = re.search(r"(AA-\d+-\d+)", pdf_url, flags=re.I)
        return pdf_url, (m.group(1).upper() if m else None), None

    raise IkeaLookupError(f"No assembly PDF found on product page: {product_url}")


def find_manual(
    query: str,
    *,
    market: str = "us",
    locale: str = "en",
    client: httpx.Client | None = None,
) -> IkeaManualRef:
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, timeout=45.0, headers=DEFAULT_HEADERS
    )
    try:
        hits = search_products(query, market=market, locale=locale, client=client)
        product = pick_best_hit(query, hits)
        pdf_url, doc_id, label = resolve_assembly_pdf(product.product_url, client=client)
        return IkeaManualRef(
            product=product,
            pdf_url=pdf_url,
            document_id=doc_id,
            label=label,
        )
    finally:
        if owns_client:
            client.close()


def download_pdf(
    pdf_url: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True, timeout=90.0, headers=DEFAULT_HEADERS
    )
    try:
        response = client.get(pdf_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
            raise IkeaLookupError(
                f"URL did not return a PDF (content-type={content_type}): {pdf_url}"
            )
        dest.write_bytes(response.content)
        return dest
    finally:
        if owns_client:
            client.close()
