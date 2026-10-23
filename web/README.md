# Alexa+ demo UI

Vite + React frontend that mimics the Alexa+ home experience for the hackathon demo.

- **Production:** `npm run build` → `web/dist` served by FastAPI at `/`
- **Dev:** start the API on `:8000`, then `npm run dev` (proxies `/v1` to the API)
- **Debug mode:** header toggle or `/?debug=1` — MCP wire, assembly context, webcam + Tier‑1 boxes

Design process for this UI follows `.cursor/skills/apple-design/`.
