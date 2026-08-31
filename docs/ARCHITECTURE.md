# Architecture

Real components only — nothing below is aspirational. See
[DEPLOYMENT.md](DEPLOYMENT.md) for how these pieces map onto services, and
[OPERATIONS.md](OPERATIONS.md) for how they're run day to day.

```mermaid
flowchart TB
    subgraph providers["External data providers"]
        squiggle["Squiggle (fixtures/results)"]
        afltables["AFL Tables (player stats)"]
        oddsapi["The Odds API (player-prop odds, optional)"]
    end

    subgraph worker["Background worker (deployment boundary)"]
        scheduler["Live-cycle scheduler\napp/player_modelling/cli.py run-scheduler\n(single-instance file lock, pause/resume)"]
    end

    subgraph webservice["Web service (deployment boundary)"]
        api["FastAPI app\nuvicorn, 2 workers, no --reload"]
        pricing["Pricing engine\nteam Elo/Poisson, player NB2/hurdle,\nSGM conditional Monte Carlo"]
        monitor["Market/SGM monitoring\napp/market_monitor, app/trading_monitor"]
        b2b["Authenticated B2B API\n/api/v1/pricing/*, /api/v1/market-intelligence/*"]
        internal["Internal product API\ndashboard, multi builder, model registry,\ntrading monitor, admin"]
    end

    subgraph db["PostgreSQL (production) / SQLite (local dev)"]
        tables["Matches, projections, PricingSnapshot,\nSgmPriceSnapshot, OddsQuote/PropMarketObservation,\nModelValueObservation, ApiUsageRecord, ..."]
    end

    subgraph frontend["Static frontend (separate hosting)"]
        spa["React SPA\nDashboard, Multi Builder, Trading Monitor,\nModel Registry, B2B Demo"]
    end

    consumers["External B2B API consumers"]

    squiggle --> scheduler
    afltables --> scheduler
    oddsapi --> scheduler
    scheduler -->|"run_live_cycle\n(idempotent, per-cycle audit row)"| db

    api --> pricing
    pricing --> db
    pricing --> monitor
    monitor --> db
    api --> b2b
    api --> internal
    b2b --> db
    internal --> db

    consumers -->|"X-API-Key"| b2b
    spa -->|"VITE_API_BASE_URL, no auth\n(same-origin product UI)"| internal
    spa -.->|"B2B Demo page only"| b2b
```

## Notes

- **The scheduler and the web service are separate processes** (a Render
  Background Worker and Web Service respectively) — the live cycle never
  runs inside a request handler, so a slow/failed cycle can't affect API
  latency or availability, and vice versa.
- **The frontend is a static build, not a container** — it has no
  server-side logic, so it's hosted as static files with an SPA rewrite
  rule, calling the backend cross-origin via `VITE_API_BASE_URL`.
- **`app/market_monitor/` and `app/trading_monitor/` are composition
  layers**, not a separate service — they read the same database the
  pricing engine writes to, read-only.
- **External consumers only ever reach the B2B API surface** (`/api/v1/pricing/*`,
  `/api/v1/market-intelligence/*`), authenticated by API key. Everything
  else under `internal` is same-origin frontend consumption — see
  [OPERATIONS.md](OPERATIONS.md#network-surface) for the full route-by-route
  breakdown.
