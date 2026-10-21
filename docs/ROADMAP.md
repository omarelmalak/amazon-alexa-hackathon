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

**Achieve**

- [x] CLI that slices an IKEA PDF into per-step diagrams (`scripts/prepare_manual.py`)
- [x] `manifest.json` + `diagrams/*.png` for **KYRRE** stool
- [x] `ProductCatalog` + `FilesystemProductCatalog`
- [x] FastAPI **read** endpoints for products / steps / BOM / diagram

**Stack:** Python, PyMuPDF, Pillow, Pydantic, FastAPI

**Exit criteria:** ~~`curl` can fetch step 2 metadata and its PNG without mentioning `.json` or file paths.~~ ✅ Verified for KYRRE.

---

## Phase 2 — Vision LLM proof of concept (current)

**Achieve**

- [x] `VisionInspector` behind an interface (`CohereVisionInspector`)
- [x] Dual-image prompt (webcam frame + step diagram) → structured JSON
- [x] In-memory `AssemblySession` + FastAPI session routes
- [x] FastAPI `POST …/inspect/orientation` (part / inventory return 501 stubs)
- [x] `scripts/smoke_inspect.py` — same diagram vs 180° flip

**Stack:** Cohere Command A Vision (structured JSON), httpx

**Exit criteria:** ~~Known good/bad photos return correct `aligned` + box; same function called from a script *and* HTTP.~~ ✅ `smoke_inspect.py` same-diagram→aligned, scribbled→not aligned.

---

## Phase 3 — Assembly session + Tier 1 client

**Achieve**

- In-memory (then durable) `AssemblySession` / state machine
- Browser HTML/JS: webcam, OpenCV/Canvas stability trigger, overlay boxes
- Client calls FastAPI only

**Exit criteria:** Idle monitoring → stability → inspect → UI paints red/green box.

---

## Phase 4 — Purchasing + orchestration polish

**Achieve**

- `PartsOrdering` + replacement-part endpoint
- Inventory inspect wired to “order missing part?” flow
- Auto-advance when vision marks step complete

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
| MCP / SSE / ngrok | Phase 5 (after HTTP green) |
| Full YOLO training | Optional; start with OpenCV stability + LLM |
| Multi-product scale | After one product (VATTENKAR or RISATORP) is demo-ready |
