# Operations

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system diagram and
[DEPLOYMENT.md](DEPLOYMENT.md) for build/migrate/deploy procedure. This
page covers running the deployed system day to day.

## Health checks

Three distinct tiers, deliberately not collapsed into one — a platform
should never restart a healthy process because of a stale bookmaker feed:

| Tier | Endpoint | Meaning | Point a platform's... |
|---|---|---|---|
| Liveness | `GET /api/health` | The process is up. Never touches the database. | ...process-liveness/restart check here |
| Readiness | `GET /api/health/db` | The database is reachable and queryable. Returns `503` on a DB error, not a generic `500`. | ...readiness/traffic-admission check here |
| Data readiness | `GET /api/v1/pricing/readiness` | Whether the pricing routes' underlying data is actually usable right now (reuses Trading Monitor's own data-health checks). `not_ready` / `degraded` / `ready`. | ...dashboards/alerting, never the restart check |

`render.yaml` points `afl-backend`'s health check at `/api/health` for
exactly this reason.

## Release provenance

`GET /api/release` returns `{git_sha, build_time, app_version, app_env}` —
safe fields only. Every B2B API response's `provenance.request_id` and
every `ApiUsageRecord` row also carries `release_sha`, so a historical
external request can be traced: request → `X-Request-ID` → `ApiUsageRecord`
row → `release_sha` → the exact commit that served it → that commit's
`docs/PRICING_ENGINE.md`/git history for what model logic was live.

## Logs

A single consistently parseable format on every logger in the process
(`app/logging_config.py`): `timestamp level=... logger=... release=<git_sha>
<message>`. B2B request completions log `request_id`, `consumer_id`,
`route`, `method`, `status`, `latency_ms` (`app/api_platform/request_context.py`).
Unhandled exceptions are logged server-side in full (`exc_info=True`,
tagged with `request_id`) while the client only ever sees a generic
`INTERNAL_ERROR` — the external error contract never changes based on what
actually broke internally.

**Never logged**: API keys (only their hash is ever stored; the raw value
is shown once at creation and never persisted), request bodies, or
personal data.

## Network surface

Not everything under `/api/*` is the same kind of surface — protecting
every route uniformly would be the wrong instinct. Four categories:

| Category | Routes | Auth |
|---|---|---|
| Public machine-readable health | `/api/health`, `/api/health/db`, `/api/release`, `/api/v1/pricing/health`, `/api/v1/pricing/model-health`, `/api/v1/pricing/readiness` | None (deliberately — uptime checks, nothing proprietary) |
| Authenticated B2B API | `/api/v1/pricing/afl/*` (except health/readiness above), `/api/v1/market-intelligence/*` | `X-API-Key` (see `docs/API_USAGE.md`) |
| Internal product UI API | `/api/afl/*`, `/api/dashboard`, `/api/matches/*`, `/api/backtest(s)`, `/api/player-models/*`, `/api/placed-bets`, `/api/bookmakers`, `/api/odds/*`, `/api/v1/model-registry`, `/api/v1/market-monitor`, `/api/v1/trading-monitor` | None — same-origin frontend consumption only, never intended for third parties even though technically reachable |
| Admin/CLI-only | `app/api_platform/cli.py` (consumer/key provisioning, usage stats), `app/player_modelling/cli.py` (live cycle, scheduler), `app/ingestion/cli.py` | Not HTTP-exposed at all |

`CORS_ORIGINS` in production is an explicit allowlist of the real frontend
origin (never `*`) — see `app/config.py`'s `validate_production_settings`,
which refuses to start with `APP_ENV=production` if this is still the
local-dev default.

## Scheduled live cycle

Runs as a GitHub Actions scheduled workflow
(`.github/workflows/live-cycle.yml`), not a Render worker — Render has no
free plan for background workers at all, and a persistent worker was a
real recurring cost this deployment avoids entirely. Every ~15 minutes
(`7,22,37,52 * * * *`, offset from the top of the hour) the workflow runs:

```bash
docker run --rm -e DATABASE_URL -e THE_ODDS_API_KEY \
  ghcr.io/srat0029-ui/afl/backend:sha-4d496ad \
  python -m app.player_modelling.cli run-live-cycle
```

— the exact same one-shot command and exit-code semantics (`0`=ok,
`1`=partial, `2`=blocked) this project has always had, from the exact same
image the backend deploys.

- **Single-instance**: a `concurrency: group: live-cycle` block in the
  workflow queues a new scheduled run rather than letting it overlap one
  still in progress — the correct equivalent of the old scheduler's file
  lock for this execution model (a file lock has no meaning across
  ephemeral GitHub-hosted runner VMs, since each run gets a fresh one).
- **Manual trigger**: `workflow_dispatch` on the same workflow, from the
  Actions tab or `gh workflow run live-cycle.yml`.
- **Failure visibility**: a failing cycle fails the GitHub Actions run
  (visible as a red X, no `|| true` masking anywhere in the workflow) —
  every cycle's outcome is also persisted to `LiveCycleRun` regardless,
  visible at `GET /api/afl/live-status`.
- **`app/player_modelling/scheduler.py`'s `run-scheduler` infinite loop
  still exists** in the codebase (useful for local development or a future
  always-on environment) but is not what production runs.
- **Known limitations** (see DEPLOYMENT.md for full detail): GitHub can
  delay a scheduled trigger under high platform load; a public repo's
  scheduled workflows auto-disable after 60 days with no *commit* activity
  (the workflow's own runs don't count); the external database may be
  hibernating between cycles, adding a few seconds to the first query.

## Backups and recovery

The external free-tier Postgres provider has **no automated backups on
its free tier** — confirmed from its own docs, not assumed. This project
covers that gap with `.github/workflows/backup.yml`: weekly, `pg_dump -Fc`
against the database, encrypted with `gpg --symmetric --cipher-algo
AES256` on the runner before anything is uploaded, verified by decrypting
and running `pg_restore --list` against the result, then uploaded as a
GitHub Actions artifact (`retention-days: 30`). Plaintext copies are
deleted from the runner immediately after use. This is genuinely free —
GitHub Actions artifact storage has no quota to exceed for a public
repository.

A manual `pg_dump`/`pg_restore` drill was separately run against a local
Postgres during the earlier CI/CD phase and confirmed a full schema + data
round-trip (all tables, a marker row, exact row-count match) — the same
mechanics the automated weekly backup and any real restore would use:

```bash
pg_dump -U <user> -d <db> -Fc -f afl_backup.dump
pg_restore -U <user> -d <db> afl_backup.dump
```

**This is not managed disaster recovery.** It's a rolling ~30-day window
of encrypted snapshots with no RPO/RTO guarantee, no automated failover,
and no continuous replication — only that the backup/restore mechanism
itself has been verified to work.

| Regeneratable (re-derivable from source data + code) | Non-regeneratable (real historical evidence — must not be lost) |
|---|---|
| Model runs/promotions, projections, current pricing | `PricingSnapshot` / `SgmPriceSnapshot` (frozen point-in-time prices) |
| Elo ratings, Poisson fits | `OddsQuote` / `PlayerPropMarket` / `PropMarketObservation` (bookmaker history) |
| Team/player feature tables | Settled outcomes (`market_result`, `settled_at` on the above) |
| Anything derivable by re-running the live cycle | `ModelValueObservation` (movement history) |
| | `ApiUsageRecord` (B2B usage history) |
| | `ApiConsumer`/`ApiKey` (consumer identity — key hashes only) |

## Cost safety

Every component in this deployment is a $0 tier with no metered dimension
that could silently accrue a charge — verified against each provider's own
docs during this phase's audit, not assumed from marketing copy:

| Component | Price | Card required | Failure mode at a limit |
|---|---|---|---|
| Render Free web service (`afl-backend`) | $0 | No | Spins down after 15 min idle; no metered overage exists on this tier |
| Render Free static site (`afl-frontend`) | $0 | No | N/A — static sites don't have this problem |
| GHCR public package | $0 | No | N/A — free for public packages, no size-based billing |
| GitHub Actions (`live-cycle.yml`, `backup.yml`, `ci.yml`) | $0 | No | N/A — free for public-repo standard runners, no quota to exceed |
| External Postgres (Free tier) | $0 | No | Hibernates / archives (see below) — degrades, never bills |

Nothing here requires a payment method anywhere, and every failure mode is
a degrade-or-pause, never an automatic charge.

## Common failure scenarios

| Scenario | Behavior today |
|---|---|
| Database unavailable at readiness check | `/api/health/db` returns `503 {"status": "error", "database": "unreachable"}` — liveness (`/api/health`) stays `200`, so the platform doesn't restart a process that's fine, just waiting on the DB. |
| Missing/unsafe production config | The app refuses to start (`validate_production_settings` in `app/config.py`) if `APP_ENV=production` and `DATABASE_URL` is still SQLite or `CORS_ORIGINS` is still the local-dev default. |
| Migration failure | `alembic upgrade` exits non-zero; the release doesn't proceed, the currently-running image keeps serving traffic unaffected (see DEPLOYMENT.md). |
| Stale market/pricing data | Surfaced honestly via `/api/v1/pricing/readiness` (`degraded`/`not_ready`) and the Trading Monitor's Data Health panel — never silently served as current. |
| Live-cycle step failure | Classified warning/recoverable/blocking per step; the scheduled workflow run goes red and `LiveCycleRun.overall_status` records exactly what happened; the next scheduled trigger tries again independently. |
| External database hibernating/archived | A cycle or request against a hibernating database sees a few extra seconds of latency while it wakes (1–5s typical); a database left fully idle for 14+ days is archived by the provider and needs an explicit manual restore — the 15-minute live-cycle schedule makes this unlikely in practice, but a long GitHub Actions outage or the 60-day schedule auto-disable (below) could let it happen. |
| GitHub Actions schedule silently disabled | Public-repo scheduled workflows auto-disable after 60 days with no *commit* activity (the workflow's own runs don't count) — requires noticing and manually re-enabling; no automated workaround is built, deliberately. |
| External Odds API unavailable/no key configured | Automated player-prop refresh reports the provider unavailable; manual prop entry and every other pricing path keep working unaffected. |
| Malformed B2B request | Unified error contract (`docs/API_USAGE.md` §6) — `400`/`422` `VALIDATION_ERROR`, never a raw traceback. |
| No promoted model for a market | `/api/v1/pricing/model-health` reports it; the affected pricing route returns `503 MODEL_UNAVAILABLE` rather than a fabricated price. |

## API consumer provisioning

CLI-only, no HTTP admin surface — see
[API_USAGE.md §12](../backend/docs/API_USAGE.md#12-provisioning-a-consumer-operator-side).
