# FastAPI Surface (validate before MCP)

All routes are thin adapters over `core` interfaces. Paths use resource nouns — **no file extensions**.

Base URL (local): `http://localhost:8000`  
API prefix: `/v1`

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
GET /v1/products/vattenkar/steps/2
```

```json
{
  "productId": "vattenkar",
  "number": 2,
  "title": "Attach side bracket",
  "requiredHardware": [
    { "partId": "114794", "name": "hex bolt", "qty": 2 }
  ],
  "completionHints": ["holes facing inward"],
  "diagramUrl": "/v1/products/vattenkar/steps/2/diagram"
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
  "boundingBox": { "x": 120, "y": 80, "w": 200, "h": 160 }
}
```

---

## Purchasing (Journey 3)

| Method | Path | Core call |
|--------|------|-----------|
| `POST` | `/v1/sessions/{sessionId}/orders/replacement-parts` | `PartsOrdering.create_replacement_order` |

---

## Mapping to MCP (later)

Each row above becomes a tool with the **same** core method — e.g. HTTP `GET …/steps/{n}` ↔ MCP `get_assembly_step(product_id, step_number)`. Tool handlers must not reimplement catalog or vision logic.

Transport for Alexa+: Streamable HTTP MCP (2025-11-25), exposed over HTTPS (e.g. ngrok) once FastAPI behavior is proven.
