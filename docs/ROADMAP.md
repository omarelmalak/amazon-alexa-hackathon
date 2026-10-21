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

- [x] `PartsOrdering` (`MockStripePartsOrdering`)
- [x] `POST …/orders/replacement-parts`
- [x] `POST …/inspect/inventory` + `…/inspect/part` (Cohere)
- [x] `GET …/bom/diagram`
- [x] UI: scan BOM / demo missing / order missing
- [x] Auto-advance on orientation `stepCompleted` (Phase 3)

**Exit criteria:** Session can report missing hardware and create a mock checkout URL.

---

## Phase 5 — MCP adapter (Alexa+)

**Achieve**

- `adapters/mcp` wrapping **existing** core interfaces
- Streamable HTTP MCP (2025-11-25) over HTTPS tunnel
- Alexa+ Developer Console → same tools proven via FastAPI
- Fire TV / Echo display of the assembly UI

**Exit criteria:** Voice “check my assembly” hits the same `VisionInspector` path as `POST /v1/sessions/.../inspect/orientation`.

---

## Explicitly deferred

| Item | Until |
|------|--------|
| Real Stripe / Amazon Pay | After mock checkout is demo-solid |
| YOLO weights | Optional; Canvas differencing is enough for Tier 1 demo |
| MCP / ngrok | Phase 5 |
