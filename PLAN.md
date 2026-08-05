# Weather Dashboard — Architecture Plan

## Overview
A lightweight, single-file Python web server (`dashboard.py`) that serves a simple weather
dashboard fetched from a public weather API. No database, no build step, no external
dependencies beyond the Python standard library.

## Components

### 1. `dashboard.py` — Web Server
- Uses `http.server` (stdlib) — no Flask/django required.
- Listens on `localhost:8080`.
- Two endpoints:
  - `GET /` → serves the HTML dashboard (inlined HTML).
  - `GET /api/weather?city=...` → returns JSON `{ city, temperature, conditions, humidity }`
    by calling `curl`/`urllib` against **wttr.in** (`/json`).
- Static HTML is embedded as a Python string — no external templates.

### 2. `PLAN.md` — Architecture Document (this file)
- Describes the system at a high level.
- Lists the components, data flow, and deployment notes.

### 3. `REVIEW.md` — Code Review
- Produced by an independent reviewer subagent.
- Covers correctness, security, error handling, and potential improvements.

## Data Flow
```
Browser ──GET /api/weather?city=Denver──► dashboard.py ──► curl wttr.in/json ──► JSON response
                                                              ◄───────────────────────┘
Dashboard UI ──renders JSON───────────────────────────────────► updates DOM
```

## Deployment
```bash
python dashboard.py      # → http://localhost:8080
```

## Future Extensions (out of scope)
- Multi-city support
- Charts for temperature history
- Docker packaging
- Authentication / API key for wttr.in
