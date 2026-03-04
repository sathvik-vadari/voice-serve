# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

**voice-serve** is a multi-LLM voice commerce service for India. Users describe what they want, the system finds nearby stores, calls them via AI phone calls, reports back with availability/pricing/delivery info, and searches for online deals — all orchestrated in parallel.

Secondary feature: personal wake-up call scheduler.

## Commands

### Backend

```bash
# Install (uses uv)
uv sync

# Run dev server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Docker
docker build -t voice-serve .
docker run -p 8000:8000 voice-serve
```

### Frontend

```bash
cd frontend

bun install       # Install dependencies
bun run dev       # Dev server at http://localhost:3000
bun run build     # Production build
bun start         # Production server
bun run lint      # ESLint
```

## Architecture

### Pipeline Flow

```
User Query → Orchestrator (OpenAI, intent classification)
  → Query Analyzer (Gemini: specific store vs generic product?)
  → Product Research (OpenAI: specs, alternatives, search terms)
  → [Parallel]
      ├── Store Finder (Google Maps) → Store Ranker (Gemini) → Store Caller (VAPI) → Transcript Analyzer (OpenAI)
      └── Web Deals (Gemini + Google Search grounding)
  → Options Summary (OpenAI, user-facing message)
  → User confirms option
  → Logistics (ProRouting: geocode → quote → book → track)
```

### Backend (`app/`)

- **`main.py`** — FastAPI entry point; initializes DB, starts background schedulers, configures CORS
- **`services/`** — Core pipeline logic: each file is one step (`orchestrator.py`, `product_research.py`, `gemini_client.py`, `store_caller.py`, `transcript_analyzer.py`, `web_deals.py`, `options_summary.py`, `logistics.py`, `wakeup_scheduler.py`)
- **`routes/`** — REST endpoints: `ticket_routes.py` (main commerce flow), `logistics_routes.py` (ProRouting callbacks), `vapi_webhook_routes.py` (call events)
- **`db/`** — Thin CRUD layer over PostgreSQL; schema auto-migrated on startup via `init_db()` in `connection.py`
- **`helpers/config.py`** — Single `Config` class that loads all env vars; always import from here
- **`helpers/regional.py`** — Per-city voice persona config (language, greeting style, TTS language) for Bangalore, Delhi, Mumbai, Chennai, Hyderabad, Kolkata
- **`prompts/*.txt`** — LLM prompt templates loaded by `helpers/prompt_loader.py`; injected at runtime with variables including `{customer_name}`
- **`schemas/vapi_tools.py`** — VAPI function tool definitions; `schemas/tool_handlers.py`— handles tool execution during live calls

### Frontend (`frontend/src/`)

- **`app/page.tsx`** — Single page with two tabs: "New Query" and "Track Order"
- **`components/query-panel.tsx`** — Query form, real-time pipeline progress, options presentation, confirm flow
- **`components/tracking-panel.tsx`** — Ticket lookup, call details, delivery tracking
- **`lib/api.ts`** — Typed API client with full TypeScript type definitions for all API shapes; always use this for API calls

### Key API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/ticket` | Create ticket, starts pipeline in background |
| `GET` | `/api/ticket/{id}` | Poll status/progress |
| `GET` | `/api/ticket/{id}/options` | Get store call + web deal results |
| `POST` | `/api/ticket/{id}/confirm` | Pick an option → triggers delivery booking |
| `GET` | `/api/ticket/{id}/delivery` | Logistics/tracking status |
| `POST` | `/api/vapi/store-webhook` | VAPI call events for store calls |
| `POST` | `/api/logistics/callback` | ProRouting delivery status callbacks |

### Database (PostgreSQL, 10 tables)

Auto-migrated on startup. Key tables:
- `tickets` — top-level request tracking
- `ticket_stores` + `store_calls` — discovered stores and per-store call records (transcript, pricing, analysis)
- `web_deals` — Gemini-powered online deal results
- `logistics_orders` — delivery order state
- `wakeup_users` + `scheduled_calls` — wake-up call scheduler
- `llm_logs` + `tool_call_logs` — full audit trail of LLM and VAPI tool calls

### Environment

Copy `.env.example` to `.env`. Required keys:
- `DATABASE_URL` — PostgreSQL connection string
- `OPENAI_API_KEY`, `OPENAI_MODEL`
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `GOOGLE_MAPS_API_KEY`
- `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_SERVER_URL`
- `PROROUTING_API_KEY`, `PROROUTING_BASE_URL`

`VAPI_SERVER_URL` must be a publicly reachable URL (e.g. ngrok) for VAPI webhooks to work in development.

`TEST_MODE=true` disables actual VAPI calls for local testing.

## Key Conventions

- **Package managers:** `uv` for Python, `bun` for frontend — do not use `pip` or `npm`
- **LLM prompts** live in `app/prompts/*.txt`, not inline in code; use `prompt_loader.py` to load them
- **Regional config** is the source of truth for city-specific voice behavior; changes to greetings/language belong in `regional.py`
- **Ticket status** progresses through a defined pipeline; `ticket_routes.py` is the best place to understand the full state machine
- **Frontend API types** are centralized in `lib/api.ts` — keep them in sync with backend response shapes
