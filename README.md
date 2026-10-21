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

## Status

Documentation and architecture locked. Implementation starts with Phase 1 (ground-truth data prep) and a FastAPI surface over core interfaces.
