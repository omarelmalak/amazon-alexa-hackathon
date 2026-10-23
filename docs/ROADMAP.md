# Implementation Roadmap

Principle: **core interfaces first → FastAPI validation → MCP adapters**. Do not couple domain logic to MCP or to filenames.

---

## Phase 0 — Docs & contracts

- [x] Product journeys
- [x] Architecture (core / http / mcp)
- [x] Data layout vs API naming
- [x] FastAPI resource design
- [x] Repo skeleton (`src/core`, `adapters/http`, `data/`)

---

## Phase 1 — Ground-truth data prep

- [x] CLI PDF → diagrams + manifest
- [x] KYRRE ground truth
- [x] `ProductCatalog` + FastAPI catalog reads
- [x] `POST /v1/products/ingest` (IKEA search → PDF → slice + optional Cohere enrich)

---

## Phase 2 — Vision LLM proof of concept

- [x] `VisionInspector` + Cohere dual-image orientation
- [x] In-memory sessions
- [x] `POST …/inspect/orientation`
- [x] `scripts/smoke_inspect.py`

---

## Phase 3 — Tier 1 webcam client

- [x] UI at `/` (`frontend/`)
- [x] Canvas stability + motion boxes
- [x] Auto/manual inspect → green/red overlay
- [x] Test match/mismatch without a physical stool

---

## Phase 4 — Purchasing + inventory (current)

**Achieve**

- [x] `PartsOrdering` (`MockIkeaSparePartsOrdering` — APO lookup + simulated checkout)
- [x] `POST …/orders/replacement-parts`
- [x] `POST …/inspect/inventory` + `…/inspect/part` (Cohere)
- [x] `GET …/bom/diagram`
- [x] UI: scan BOM / demo missing / order missing
- [x] Auto-advance on orientation `stepCompleted` (Phase 3)

**Exit criteria:** Session can report missing hardware and create a mock checkout URL.

---

## Phase 5 — MCP adapter (Alexa+)

**Achieve**

- [x] `adapters/mcp` wrapping **existing** core interfaces
- [x] Streamable HTTP MCP mounted at `/mcp` (same uvicorn process as FastAPI)
- [x] Web MCP client simulator at `/sim` (hackathon path — real initialize/tools/list/tools/call)
- [ ] Optional: HTTPS tunnel (ngrok) for remote judges
- [ ] Optional: Fire TV / Echo display of the assembly UI

**Exit criteria:** Simulated “check my assembly” hits the same `VisionInspector` path as `POST /v1/sessions/.../inspect/orientation` via MCP `inspect_orientation`.

---

## Explicitly deferred

| Item | Until |
|------|--------|
| Real Stripe / Amazon Pay | After mock checkout is demo-solid |
| YOLO weights | Optional; Canvas differencing is enough for Tier 1 demo |
| Alexa+ console wiring | After `/mcp` smoke passes locally |
