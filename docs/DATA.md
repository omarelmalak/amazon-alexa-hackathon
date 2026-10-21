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
  vattenkar/
    source/
      manual.pdf              # original IKEA PDF (optional to keep)
    diagrams/
      bom.png                 # optional hardware overview page
      01.png
      02.png
      …
    manifest.json             # product contract for the loader
```

### `manifest.json` shape (loader contract, not URL)

```json
{
  "productId": "vattenkar",
  "displayName": "VATTENKAR",
  "manualSource": "source/manual.pdf",
  "bom": [
    { "partId": "114794", "name": "hex bolt", "qty": 6 }
  ],
  "steps": [
    {
      "number": 1,
      "diagram": "diagrams/01.png",
      "title": "Attach side bracket",
      "requiredHardware": [
        { "partId": "114794", "name": "hex bolt", "qty": 2 }
      ],
      "completionHints": [
        "side panel attached",
        "pre-drilled holes facing inward"
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
| PDF → PNG | PyMuPDF (`fitz`) | Render pages at controlled DPI |
| Image trim (optional) | Pillow | Crop margins |
| CLI | Python (`typer` or `argparse`) | `prepare-manual data/vattenkar/…` |
| Schema check (optional) | Pydantic | Validate manifest before services load it |

IKEA manuals are mostly wordless → rasterize automatically; **curate** part IDs / qty in the manifest once. Full OCR of every fastener is out of scope for Phase 1.
