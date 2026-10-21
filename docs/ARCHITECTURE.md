# Architecture

## Two-tier hybrid vision

Avoid streaming 30 FPS to a cloud LLM. Coordinate with an explicit state machine.

### Tier 1 — Local bounding-box engine (edge)

| | |
|---|---|
| **Stack** | OpenCV.js or lightweight YOLOv8 in browser/client |
| **Rate** | Continuous 15–30 FPS |
| **Job** | Draw boxes around hands, panels, screws — **no step awareness** |
| **Trigger** | Stability (motion stopped ~1.2–1.5s) → wake Tier 2 |
| **UI** | Live tracking overlays for responsiveness |

### Tier 2 — Multimodal spatial checker (cloud)

| | |
|---|---|
| **Stack** | Vision LLM (GPT-4o / Claude) behind core service interfaces |
| **When** | Tier 1 stability **or** Alexa / user verbal trigger |
| **Payload** | One high-res webcam frame + current-step diagram (from ground truth) |
| **Contract** | Strict JSON: `{ aligned, error_description, bounding_box }` |

---

## Interface-first layering (critical)

We do **not** build MCP-shaped APIs first. Domain logic is isolated so HTTP and MCP stay thin.

```
┌─────────────────────────────────────────────────────────┐
│  Clients: Browser / Fire TV UI / Alexa+                 │
└─────────────────────┬─────────────────┬─────────────────┘
                      │                 │
           ┌──────────▼──────┐   ┌──────▼──────────┐
           │  adapters/http  │   │  adapters/mcp   │
           │  (FastAPI)      │   │  (later)        │
           └──────────┬──────┘   └──────┬──────────┘
                      │                 │
                      └────────┬────────┘
                               │
                    ┌──────────▼──────────┐
                    │  core/              │
                    │  interfaces + impl  │
                    │  (shared)           │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  data/  (files)     │
                    │  LLM / Stripe / …   │
                    └─────────────────────┘
```

### Package layout (target)

```
src/
  core/
    models/           # AssemblyState, Step, HardwarePart, VerifyResult
    interfaces/       # Protocols / ABCs — what adapters call
    services/         # CatalogService, AssemblyService, VisionVerifyService, …
    state/            # AssemblyState machine
  adapters/
    http/             # FastAPI routers → call core interfaces only
    mcp/              # MCP tools → same core interfaces (Phase 5+)
  infra/              # filesystem loaders, OpenAI/Anthropic clients, Stripe
data/
  <productId>/        # ground-truth assets (not the API)
```

### Core interfaces (illustrative)

Adapters depend on these — never on filesystem paths or “`.json`” filenames:

| Interface | Responsibility |
|-----------|----------------|
| `ProductCatalog` | List products; load BOM; load step metadata; resolve diagram bytes |
| `AssemblySession` | Get/update `AssemblyState`; advance step; set mode |
| `VisionInspector` | Verify orientation / part ID / inventory from a frame + step context |
| `PartsOrdering` | Start replacement-part purchase (Stripe / Amazon) |

**Rule:** FastAPI route handlers and MCP tool handlers only translate I/O ↔ interface calls. No duplicate business logic.

### Validation order

1. Implement `core` + filesystem `infra`
2. Expose via **FastAPI** and test with curl / Postman / browser
3. Wrap the **same interfaces** with Streamable HTTP MCP for Alexa+

---

## What is *not* an endpoint

On-disk layout like `data/VATTENKAR/diagrams/03.png` and `data/VATTENKAR/manifest.json` is **storage**, not the public API. See [DATA.md](DATA.md) and [API.md](API.md).
