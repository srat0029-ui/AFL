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

Runs as its own long-lived process (`afl-live-cycle-scheduler` background
worker in `render.yaml`), never inside a web request:

```bash
python -m app.player_modelling.cli run-scheduler --interval-minutes 15
```

- **Single-instance**: an exclusive-create file lock
  (`backend/live_cycle_scheduler.lock`) prevents two instances of this
  process running at once. If a previous instance crashed without cleaning
  up, delete the lock file before restarting.
- **Pause without stopping**: `python -m app.player_modelling.cli pause-scheduler` /
  `resume-scheduler` (a sentinel file the running loop checks each tick).
- **Manual one-shot run** (bypassing the scheduler entirely): `python -m
  app.player_modelling.cli run-live-cycle` — exits `0` (ok) / `1` (partial)
  / `2` (blocked), matching real cycle outcomes, not just process success.
- **Failure visibility**: one bad cycle logs the exception and continues to
  the next tick rather than killing the process (see
  `app/player_modelling/scheduler.py`); every cycle's outcome is also
  persisted to `LiveCycleRun` regardless of success/failure, visible at
  `GET /api/afl/live-status`.

## Backups and recovery

Render's managed Postgres includes automated daily backups with the plan's
standard retention window — this project doesn't operate its own backup
infrastructure on top of that. A manual `pg_dump`/`pg_restore` drill was
run against a local Postgres during this phase and confirmed a full
schema + data round-trip (all tables, a marker row, exact row-count match)
— this is a tested procedure, not a claimed one:

```bash
pg_dump -U <user> -d <db> -Fc -f afl_backup.dump
pg_restore -U <user> -d <db> afl_backup.dump
```

**No disaster-recovery guarantee (RPO/RTO, automated failover, etc.) is
claimed** — only that the basic backup/restore mechanism itself works.

| Regeneratable (re-derivable from source data + code) | Non-regeneratable (real historical evidence — must not be lost) |
|---|---|
| Model runs/promotions, projections, current pricing | `PricingSnapshot` / `SgmPriceSnapshot` (frozen point-in-time prices) |
| Elo ratings, Poisson fits | `OddsQuote` / `PlayerPropMarket` / `PropMarketObservation` (bookmaker history) |
| Team/player feature tables | Settled outcomes (`market_result`, `settled_at` on the above) |
| Anything derivable by re-running the live cycle | `ModelValueObservation` (movement history) |
| | `ApiUsageRecord` (B2B usage history) |
| | `ApiConsumer`/`ApiKey` (consumer identity — key hashes only) |

## Common failure scenarios

| Scenario | Behavior today |
|---|---|
| Database unavailable at readiness check | `/api/health/db` returns `503 {"status": "error", "database": "unreachable"}` — liveness (`/api/health`) stays `200`, so the platform doesn't restart a process that's fine, just waiting on the DB. |
| Missing/unsafe production config | The app refuses to start (`validate_production_settings` in `app/config.py`) if `APP_ENV=production` and `DATABASE_URL` is still SQLite or `CORS_ORIGINS` is still the local-dev default. |
| Migration failure | `alembic upgrade` exits non-zero; the release doesn't proceed, the currently-running image keeps serving traffic unaffected (see DEPLOYMENT.md). |
| Stale market/pricing data | Surfaced honestly via `/api/v1/pricing/readiness` (`degraded`/`not_ready`) and the Trading Monitor's Data Health panel — never silently served as current. |
| Live-cycle step failure | Classified warning/recoverable/blocking per step; the scheduler logs and retries next tick; `LiveCycleRun.overall_status` records exactly what happened. |
| External Odds API unavailable/no key configured | Automated player-prop refresh reports the provider unavailable; manual prop entry and every other pricing path keep working unaffected. |
| Malformed B2B request | Unified error contract (`docs/API_USAGE.md` §6) — `400`/`422` `VALIDATION_ERROR`, never a raw traceback. |
| No promoted model for a market | `/api/v1/pricing/model-health` reports it; the affected pricing route returns `503 MODEL_UNAVAILABLE` rather than a fabricated price. |

## API consumer provisioning

CLI-only, no HTTP admin surface — see
[API_USAGE.md §12](../backend/docs/API_USAGE.md#12-provisioning-a-consumer-operator-side).
