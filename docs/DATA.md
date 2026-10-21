# Ground-Truth Data (Phase 1)

## Clarification: files ≠ API

Earlier notes mentioned:

- `data/<productId>/steps/*.png`
- `data/<productId>/steps.json`

Those are **on-disk assets produced by Phase 1**, not MCP tools and not HTTP paths. Clients never request “`steps.json`”. They call REST (then later MCP) resources with clear names.

| Concern | On disk (implementation detail) | Public surface (HTTP / MCP) |
|---------|----------------------------------|-----------------------------|
| Step diagrams | PNG files under a product folder | `GET /products/{id}/steps/{n}/diagram` |
| Step + hardware metadata | One product manifest file | `GET /products/{id}/steps`, `GET /products/{id}/steps/{n}` |
| Full hardware list | Same manifest (`bom` section) | `GET /products/{id}/bom` |

The two artifacts differ by **role**:

1. **Diagram images** — pixels the UI and Vision LLM compare against the webcam. One image ≈ one manual illustration.
2. **Manifest** — structured metadata: step order, part IDs, quantities, optional completion hints. Loaded by `ProductCatalog`; never exposed as a raw “`.json` download” in the product API (unless we add an explicit export later).

Avoid naming both “steps” at the top level on disk so the mental model stays clear.

---

## Proposed on-disk layout

```
data/
  kyrre/                      # first product (IKEA stool)
    source/
      manual.pdf
    diagrams/
      cover.png               # optional product cover
      bom.png                 # hardware overview page
      01.png
      02.png
    manifest.json
```

### `manifest.json` shape (loader contract, not URL)

```json
{
  "productId": "kyrre",
  "displayName": "KYRRE",
  "manualSource": "source/manual.pdf",
  "bom": [
    { "partId": "130915", "name": "hex socket screw", "qty": 6 },
    { "partId": "100092", "name": "allen key", "qty": 1 },
    { "partId": "112357", "name": "plastic plug", "qty": 3 }
  ],
  "steps": [
    {
      "number": 1,
      "diagram": "diagrams/01.png",
      "title": "Attach legs to seat",
      "requiredHardware": [
        { "partId": "130915", "name": "hex socket screw", "qty": 6 },
        { "partId": "100092", "name": "allen key", "qty": 1 }
      ],
      "completionHints": [
        "three legs fastened to underside of seat",
        "screw heads flush with wood"
      ]
    }
  ]
}
```

### Naming rules

- **Disk:** `manifest.json`, `diagrams/NN.png` — fine; these are files.
- **HTTP:** `/products/{productId}/steps/{stepNumber}` — no `.json`, no `.png` in the resource name; use content negotiation / dedicated `/diagram` sub-resource for images.
- **MCP (later):** tool names like `get_assembly_step`, `get_step_diagram`, `list_bom` — mirror the interfaces, not filenames.

---

## Phase 1 tooling

| Piece | Choice | Role |
|-------|--------|------|
| PDF → PNG | PyMuPDF | Render pages at controlled DPI |
| Image trim (optional) | Pillow | Crop margins |
| CLI | `scripts/prepare_manual.py` | Local PDF → diagrams + manifest |
| HTTP ingest | `POST /v1/products/ingest` | Product name → IKEA search → PDF → same pipeline |
| LLM enrich (optional) | Cohere Command A Vision (`COHERE_API_KEY`) | Fill names, titles, `completionHints` from diagram PNGs (base64) |
| Schema | Pydantic | Validate manifest before services load it |

IKEA manuals are mostly wordless. Rasterize automatically; **curate** (or set `enrich=true`) for part names / completion hints. Web PDFs sometimes lack extractable text on BOM/step pages — diagrams are still sliced; enrichment recovers metadata from the images.
