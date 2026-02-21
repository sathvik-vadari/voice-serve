# voice-serve

Multi-LLM voice commerce service for India — order anything via a phone call.

A user describes what they want, and voice-serve finds nearby stores, **calls them on the phone**, and reports back with availability, pricing, and delivery info. It also doubles as a personal wake-up call scheduler.

## How It Works

```
User submits query
        │
        ▼
┌──────────────┐
│ Orchestrator  │  ← OpenAI: classify intent (order vs. wake-up)
└──────┬───────┘
       │ order_product
       ▼
┌──────────────┐
│ Query Analyzer│  ← Gemini: specific store vs. generic product?
└──────┬───────┘
       ▼
┌──────────────┐
│ Product       │  ← OpenAI: extract specs, alternatives, search terms
│ Research      │
└──────┬───────┘
       ▼
┌──────────────┐
│ Store Finder  │  ← Google Maps: multi-strategy search + dedup
└──────┬───────┘
       ▼
┌──────────────┐
│ Store Ranker  │  ← Gemini: re-rank by relevance
└──────┬───────┘
       ▼
┌──────────────┐
│ Store Caller  │  ← VAPI: parallel outbound phone calls
└──────┬───────┘
       ▼
┌──────────────┐
│ Transcript    │  ← OpenAI: structured extraction from call transcripts
│ Analyzer      │
└──────┬───────┘
       ▼
  Final result: best match with price, delivery, and match score
```

## Tech Stack

| Layer | Tech |
|-------|------|
| Framework | FastAPI + Uvicorn |
| LLMs | OpenAI GPT-4o, Google Gemini |
| Voice / Telephony | VAPI (Deepgram transcription, Cartesia TTS) |
| Store Discovery | Google Maps Places API |
| Database | PostgreSQL |
| Language | Python 3.12+ |

## Project Structure

```
app/
├── main.py                  # FastAPI entry point, lifespan, CORS
├── db/
│   ├── connection.py        # Postgres connection & schema init
│   ├── tickets.py           # Commerce ticket CRUD
│   └── wakeup.py            # Wake-up call CRUD
├── helpers/
│   ├── config.py            # Env config loader
│   ├── logger.py            # Logging setup
│   ├── prompt_loader.py     # Loads .txt prompt files
│   └── regional.py          # Per-city language & persona config
├── prompts/                 # LLM prompt templates (.txt)
├── routes/
│   ├── ticket_routes.py     # REST API endpoints
│   └── vapi_webhook_routes.py  # VAPI event handlers
├── schemas/
│   ├── tool_handlers.py     # Tool execution logic
│   └── vapi_tools.py        # VAPI function definitions
├── services/
│   ├── orchestrator.py      # Intent classification
│   ├── product_research.py  # Product detail extraction
│   ├── gemini_client.py     # Query analysis & store re-ranking
│   ├── google_maps.py       # Place search + dedup
│   ├── store_caller.py      # Outbound call orchestration
│   ├── transcript_analyzer.py  # Post-call structured extraction
│   ├── vapi_client.py       # VAPI API wrapper
│   └── wakeup_scheduler.py  # Background scheduler for wake-up calls
└── scripts/
    └── retry_scheduled_call.py
```

## API Endpoints

### Tickets

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ticket` | Create a ticket — kicks off the full pipeline in the background |
| `GET` | `/api/ticket/{ticket_id}` | Poll for status, progress, and results |

**Create ticket payload:**

```json
{
  "query": "I need a 2kg Prestige pressure cooker",
  "location": "Indiranagar, Bangalore",
  "user_phone": "+919876543210"
}
```

### VAPI Webhooks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/vapi/webhook` | Wake-up call events |
| `POST` | `/api/vapi/store-webhook` | Store inquiry call events |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL
- A [VAPI](https://vapi.ai) account with a phone number
- API keys for OpenAI, Google Gemini, and Google Maps
- A public URL for webhooks (e.g. ngrok during development)

### Install

```bash
git clone https://github.com/<you>/voice-serve.git
cd voice-serve
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Configure

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
```

You'll need API keys for OpenAI, Google Maps, Google Gemini, and VAPI. See [`.env.example`](.env.example) for all available options.

### Run

```bash
python -m app.main
```

The server starts on `http://0.0.0.0:8000` by default. The database schema is auto-created on first boot.

## Database Schema

Seven tables, auto-migrated on startup:

| Table | Purpose |
|-------|---------|
| `tickets` | Top-level request tracking (query, status, result) |
| `ticket_products` | Extracted product details & specs (JSONB) |
| `ticket_stores` | Discovered stores with location & call priority |
| `store_calls` | Per-store call records: transcript, analysis, pricing |
| `wakeup_users` | User preferences (daily wake-up time, do-not-call) |
| `scheduled_calls` | Pending/completed wake-up calls |
| `llm_logs` | Full LLM call audit trail (prompt, response, tokens, latency) |

## Regional Support

voice-serve speaks the customer's language. Regional config auto-detects city from the location string and adjusts:

- **Language** — Hindi, Kannada, Tamil, Telugu, Bengali, English
- **Voice** — TTS language matching the region
- **Greeting style** — culturally appropriate openers
- **Communication style** — adapted per region


## License

MIT
