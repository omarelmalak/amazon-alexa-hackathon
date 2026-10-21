# FastAPI Surface (validate before MCP)

All routes are thin adapters over `core` interfaces. Paths use resource nouns — **no file extensions**.

Base URL (local): `http://localhost:8000`  
API prefix: `/v1`

---

## Ingest (find + download + process)

Alexa+ / agents should call this tool — they do **not** scrape IKEA themselves.

| Method | Path | Core call | Notes |
|--------|------|-----------|-------|
| `POST` | `/v1/products/ingest` | `ManualIngester.ingest` | Search IKEA → PDF → diagrams + manifest |

```http
POST /v1/products/ingest
Content-Type: application/json

{
  "query": "KYRRE",
  "market": "us",
  "locale": "en",
  "force": false,
  "enrich": true
}
```

| Field | Meaning |
|-------|---------|
| `query` | Product series/name spoken by the user |
| `force` | Overwrite `data/<productId>/` if it already exists |
| `enrich` | If true and `COHERE_API_KEY` is set, Command A Vision fills names/titles/`completionHints` from diagram PNGs |

Pipeline: IKEA search API → product page → assembly PDF URL → download → `prepare_manual` slicer → optional Cohere vision enrich → readable via catalog routes below.

---

## Catalog (reads ground truth)

| Method | Path | Core call | Returns |
|--------|------|-----------|---------|
| `GET` | `/v1/products` | `ProductCatalog.list_products` | Product summaries |
| `GET` | `/v1/products/{productId}` | `ProductCatalog.get_product` | Display name, step count, etc. |
| `GET` | `/v1/products/{productId}/bom` | `ProductCatalog.get_bom` | Hardware bill of materials |
| `GET` | `/v1/products/{productId}/steps` | `ProductCatalog.list_steps` | Step list (metadata only) |
| `GET` | `/v1/products/{productId}/steps/{stepNumber}` | `ProductCatalog.get_step` | One step + required hardware |
| `GET` | `/v1/products/{productId}/steps/{stepNumber}/diagram` | `ProductCatalog.get_diagram` | Image bytes (`image/png`) |

### Example

```http
GET /v1/products/kyrre/steps/2
```

```json
{
  "productId": "kyrre",
  "number": 2,
  "title": "Insert plastic plugs",
  "requiredHardware": [
    { "partId": "112357", "name": "plastic plug", "qty": 3 }
  ],
  "completionHints": ["one plastic plug seated at each leg junction"],
  "diagramUrl": "/v1/products/kyrre/steps/2/diagram"
}
```

---

## Assembly session (physical-world state)

| Method | Path | Core call | Notes |
|--------|------|-----------|-------|
| `POST` | `/v1/sessions` | `AssemblySession.create` | Body: `{ "productId" }` → session id + initial state |
| `GET` | `/v1/sessions/{sessionId}` | `AssemblySession.get_state` | Full `AssemblyState` |
| `PATCH` | `/v1/sessions/{sessionId}` | `AssemblySession.update` | Mode, step overrides (debug) |
| `POST` | `/v1/sessions/{sessionId}/advance` | `AssemblySession.advance` | Move to next step when complete |

---

## Vision inspection (Tier 2)

| Method | Path | Core call | Notes |
|--------|------|-----------|-------|
| `POST` | `/v1/sessions/{sessionId}/inspect/orientation` | `VisionInspector.verify_orientation` | Frame + current step diagram |
| `POST` | `/v1/sessions/{sessionId}/inspect/part` | `VisionInspector.identify_part` | “Which screw is this?” |
| `POST` | `/v1/sessions/{sessionId}/inspect/inventory` | `VisionInspector.count_parts` | “Scan my parts” |

Request body pattern (multipart or JSON):

```json
{
  "frameBase64": "<…>",
  "stepNumber": 2
}
```

Orientation response:

```json
{
  "aligned": false,
  "errorDescription": "Bracket appears upside down; recessed groove faces up",
  "boundingBox": { "x": 120, "y": 80, "w": 200, "h": 160 },
  "confidence": 0.91,
  "stepCompleted": false,
  "rawNotes": null
}
```

`inspect/part` and `inspect/inventory` currently return **501** stubs.

Local smoke test (no webcam needed): `python scripts/smoke_inspect.py kyrre 1`

---

## Purchasing (Journey 3)

| Method | Path | Core call |
|--------|------|-----------|
| `POST` | `/v1/sessions/{sessionId}/orders/replacement-parts` | `PartsOrdering.create_replacement_order` |

---

## Mapping to MCP (later)

Each row above becomes a tool with the **same** core method — e.g. HTTP `GET …/steps/{n}` ↔ MCP `get_assembly_step(product_id, step_number)`. Tool handlers must not reimplement catalog or vision logic.

Transport for Alexa+: Streamable HTTP MCP (2025-11-25), exposed over HTTPS (e.g. ngrok) once FastAPI behavior is proven.
