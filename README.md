# IKEA Flatpack Co-Pilot

Autonomous assembly assistant for the Amazon Alexa+ / Fire TV Developer Hackathon. Helps users assemble IKEA furniture (e.g. VATTENKAR, RISATORP) with camera-based spatial checks, hardware ID, inventory tracking, and purchasing flows.

## Docs

| Doc | Purpose |
|-----|---------|
| [docs/PRODUCT.md](docs/PRODUCT.md) | Product definition & core user journeys |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layered design: core interfaces → FastAPI → MCP |
| [docs/DATA.md](docs/DATA.md) | On-disk ground truth vs HTTP/MCP surface |
| [docs/API.md](docs/API.md) | Raw FastAPI endpoints (validate first) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Implementation phases |

## Design principle

Business logic lives behind **stable interfaces** in a shared core. FastAPI and MCP are thin adapters over the same code — we prove behavior with raw HTTP first, then expose the same operations as MCP tools for Alexa+.

```
core/          ← domain + interfaces (source of truth)
adapters/http  ← FastAPI
adapters/mcp   ← Streamable HTTP MCP (later)
```

## Local backend server

### 1. One-time setup

```bash
cd amazon-alexa-hackathon
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional — needed for Cohere enrich on ingest
# Edit .env and set COHERE_API_KEY=...
```

See [`.env.example`](.env.example) for supported variables.

### 2. Start the FastAPI server

From the repo root, with the venv activated:

```bash
PYTHONPATH=src uvicorn adapters.http.app:app --reload --host 127.0.0.1 --port 8000
```

| | |
|---|---|
| Base URL | http://127.0.0.1:8000 |
| Health | http://127.0.0.1:8000/health |
| Interactive docs | http://127.0.0.1:8000/docs |
| OpenAPI JSON | http://127.0.0.1:8000/openapi.json |

`--reload` restarts the process when Python files change. Stop the server with `Ctrl+C`.

### 3. Smoke-test catalog endpoints

KYRRE ground truth is already under `data/kyrre/`. With the server running:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/products
curl http://127.0.0.1:8000/v1/products/kyrre
curl http://127.0.0.1:8000/v1/products/kyrre/bom
curl http://127.0.0.1:8000/v1/products/kyrre/steps
curl http://127.0.0.1:8000/v1/products/kyrre/steps/1
curl -o step1.png http://127.0.0.1:8000/v1/products/kyrre/steps/1/diagram
```

Full route list: [docs/API.md](docs/API.md).

### 4. Ingest a product by name (IKEA search → PDF → diagrams)

Alexa+ will call this as a tool later. For now, exercise it over HTTP:

```bash
# Deterministic slice only (no Cohere key required)
curl -s -X POST http://127.0.0.1:8000/v1/products/ingest \
  -H 'Content-Type: application/json' \
  -d '{"query":"KYRRE","enrich":false,"force":true}' | python3 -m json.tool

# Same + Cohere Command A Vision curation of names / titles / completionHints
# (loads COHERE_API_KEY from .env when the server starts)
curl -s -X POST http://127.0.0.1:8000/v1/products/ingest \
  -H 'Content-Type: application/json' \
  -d '{"query":"KYRRE","enrich":true,"force":true}' | python3 -m json.tool
```

Diagrams are sent as `data:image/png;base64,...` — Cohere vision models accept that format and can read IKEA line art / part numbers (use `detail=high` in our client for small text).

Then read it back with the catalog routes in step 3.

### Optional: re-slice a local PDF

If you already have a PDF on disk:

```bash
python scripts/prepare_manual.py path/to/manual.pdf \
  --product-id kyrre \
  --display-name KYRRE
```

## Alexa+ and scraping — who does what?

| Actor | Role |
|-------|------|
| **Alexa+** | Agent: understands “help me build my Kyrre stool”, picks the tool, passes `query=KYRRE` |
| **Our backend** (`POST /v1/products/ingest`) | Searches IKEA, downloads the assembly PDF, slices diagrams, optional LLM enrich |
| **Later: MCP adapter** | Same `ManualIngester.ingest` exposed as a Streamable HTTP MCP tool for Alexa+ |

Alexa+ does **not** browse ikea.com or pull PDFs on its own unless you give it a tool that does that. **You need this ingest endpoint** (and later the MCP wrapper). To test today: curl / Swagger. To test with Alexa+: tunnel HTTPS + register the MCP tool once Phase 5 is wired — the agent just invokes `ingest` / `get_assembly_step`; it never scrapes.

## Status

Phase 1: KYRRE ground truth, FastAPI catalog reads, and `POST /v1/products/ingest` for name→manual ingestion.
