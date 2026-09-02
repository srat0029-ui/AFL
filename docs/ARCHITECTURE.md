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

    subgraph ci["GitHub CI (public repo, free)"]
        build["GitHub Actions: test, lint,\nDocker build"]
        ghcr["GHCR: ghcr.io/.../afl/backend\nimmutable, digest-pinned image"]
        build --> ghcr
    end

    subgraph gha["GitHub Actions scheduled workflows (free, public repo)"]
        livecycle["live-cycle.yml\nevery ~15 min, offset from :00\ndocker run <pinned image> run-live-cycle\nconcurrency group: no overlap"]
        backup["backup.yml\nweekly: pg_dump -> gpg AES256 encrypt\n-> Actions artifact (30-day retention)"]
    end

    subgraph webservice["Render Free web service (runtime: image)"]
        api["FastAPI app\nuvicorn, no --reload\nruns the SAME pinned GHCR image"]
        pricing["Pricing engine\nteam Elo/Poisson, player NB2/hurdle,\nSGM conditional Monte Carlo"]
        monitor["Market/SGM monitoring\napp/market_monitor, app/trading_monitor"]
        b2b["Authenticated B2B API\n/api/v1/pricing/*, /api/v1/market-intelligence/*"]
        internal["Internal product API\ndashboard, multi builder, model registry,\ntrading monitor, admin"]
    end

    subgraph db["External free-tier PostgreSQL\n(hibernates when idle, wakes on connect)"]
        tables["Matches, projections, PricingSnapshot,\nSgmPriceSnapshot, PropMarketObservation,\nModelValueObservation, ApiUsageRecord, ..."]
    end

    subgraph frontend["Render Free static site"]
        spa["React SPA\nDashboard, Multi Builder, Trading Monitor,\nModel Registry, B2B Demo"]
    end

    consumers["External B2B API consumers"]

    ghcr -.->|"pulled, not rebuilt"| api
    ghcr -.->|"pulled, not rebuilt"| livecycle

    squiggle --> livecycle
    afltables --> livecycle
    oddsapi --> livecycle
    livecycle -->|"run_live_cycle\n(idempotent, per-cycle audit row)"| db
    backup -->|"read-only pg_dump"| db

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

- **There is no Render background worker and no Render Postgres.** Both
  were real recurring-cost sources (Render has no free plan for background
  workers at all, and free Render Postgres expires 30 days after creation
  with no backups) — see docs/DEPLOYMENT.md's "$0 architecture" section for
  the full reasoning. The live cycle runs as a scheduled GitHub Actions
  workflow instead of a persistent worker process; the database is an
  external free-tier Postgres provider instead of Render Postgres.
- **The backend deploys a pinned GHCR image (`runtime: image`), never a
  Render-side rebuild.** The scheduled live-cycle workflow pulls the exact
  same image — one CI-verified artifact, two consumers, never two
  independently-built copies of the same code.
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
