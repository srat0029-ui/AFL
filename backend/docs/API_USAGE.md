# API Usage Guide

Audience: an engineering team integrating with this API from another codebase.
For modelling methodology and evaluation evidence, see [PRICING_ENGINE.md](PRICING_ENGINE.md)
and [B2B_DEMO.md](../../B2B_DEMO.md). This page is about how to *call* the API, not
what the numbers mean.

## 1. Interactive spec

The API is FastAPI-backed, so a live, always-in-sync OpenAPI spec and interactive
docs are generated automatically — no separate spec to maintain or go stale:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Raw OpenAPI JSON: `GET /openapi.json`

Point any codegen tool (openapi-generator, orval, etc.) at `/openapi.json` to
generate a typed client directly.

## 2. Endpoint groups relevant to a B2B integration

| Group | Base path | Purpose |
|---|---|---|
| Pricing | `/api/v1/pricing/*` | Pure model prices — team, disposals, goals |
| Market intelligence | `/api/v1/market-intelligence/*` | Model vs. live bookmaker comparison |
| Model registry | `/api/v1/model-registry*` | Champion/challenger history, prospective live evaluation |
| Integration health | `/api/v1/integration-health` | Operational status for monitoring |

Full parameter lists and response schemas: see `/docs`. Endpoint list and
example payloads: see [PRICING_ENGINE.md §2](PRICING_ENGINE.md) and
[`docs/api_examples/`](api_examples/) (real, captured responses, not
hand-written samples).

## 3. Authentication

The `/api/v1/pricing/*` and `/api/v1/market-intelligence/*` routes require a
per-consumer API key sent as an `X-API-Key` header, checked by a FastAPI
dependency (`require_api_key`) ahead of the route body — the same
`Depends(get_db)` shape already used for the DB session. `/health` and
`/model-health` stay open (standard for uptime checks, nothing proprietary
in the response).

- Request a key out-of-band from whoever operates this deployment. Keys are
  provisioned with the admin CLI (`python -m app.api_platform.cli
  create-consumer` / `create-key`) — there is no self-service signup or HTTP
  key-management endpoint.
- A key is only ever shown once, at creation time. The server stores a
  SHA-256 hash, never the raw value — a lost key means issuing a new one.
- A missing or invalid key gets `401` with the shared error shape (§6).
- **Local development bypass**: when the server is running with
  `APP_ENV=local` (the same setting that already loosens CORS) and no
  `X-API-Key` header is sent, the request is treated as a synthetic
  `local-dev` consumer instead of being rejected. This is why the frontend
  and the existing test suite need no key at all when running locally. Any
  other environment always requires a real key, and a request that *does*
  send a key is validated for real even in local mode.

## 4. Rate limiting

Each API key carries a per-minute limit and a rolling 24-hour quota (default
60/minute, 5,000/day — set per-consumer at provisioning time). Limits are
enforced in-process against a DB-backed usage log (`ApiUsageRecord`), not a
separate cache or gateway — this is a rolling-window, single-database limit,
not a distributed one, and is sized for this project's real traffic rather
than gateway-scale throughput.

Every authenticated response carries the current status as headers:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
X-RateLimit-Daily-Quota: 5000
X-RateLimit-Daily-Remaining: 4991
```

Exceeding either limit returns `429` with the shared error shape and a
`retry_after_seconds` field in `details`.

## 4a. Request tracing and readiness

Every response carries an `X-Request-ID` header (a client-supplied one is
echoed back; otherwise the server generates one). The same ID appears in
every pricing response's `provenance.request_id` field and in any error
body — quote it when reporting an issue, it identifies the exact server-side
usage-log row.

`GET /api/v1/pricing/readiness` (unauthenticated, like `/health`) reports
whether the underlying data the pricing routes depend on is actually usable
right now — reusing the same data-health checks the internal Trading
Monitor uses, not a separate freshness implementation. `status` is
`"not_ready"` if any check is at error severity, `"degraded"` if only
warnings exist, `"ready"` otherwise:

```json
{"status": "ready", "generated_at": "2026-08-23T13:10:48.015909+00:00", "checks": []}
```

## 5. Expected response times

Measured in-process (excludes HTTP/dev-server overhead) via
[`scripts/benchmark_pricing.py`](../scripts/benchmark_pricing.py) against the
real current-round dataset. Re-run it any time — it's read-only:

```bash
python -m scripts.benchmark_pricing
```

| Operation | p50 | p95 | Notes |
|---|---|---|---|
| Single team-market pricing | 0.3ms | 1.8ms | Elo/Poisson context is pre-fit, not re-fit per request; n=20 reps |
| Full round pricing, cache miss | ~110ms | — | single-shot (repeating would hit the cache); scales with matches × players in the round |
| Full round pricing, cache hit | <1ms | — | single-shot; 30s TTL cache around the whole round response |
| Arbitrary player-threshold query | 0.8ms | 1.2ms | evaluates one persisted distribution at any threshold; n=20 reps |
| Same Game Multi, 2 legs, default 100k sims | 14.2ms | 15.1ms | Monte Carlo pricing; n=20 reps |
| Same Game Multi, 3 legs, default 100k sims | 16.2ms | 16.9ms | n=20 reps |
| Same Game Multi, 2 legs, 200k sims (API max) | 28.1ms | 28.7ms | `n_simulations` is capped at 200,000; n=20 reps |

Figures above are one real measurement from `scripts/benchmark_pricing.py`
against the current dev dataset — re-run it yourself rather than trusting
these as a permanent SLA, since they scale with whatever match/player data
happens to be loaded. These are in-process compute figures. Observed HTTP round-trip time in this
dev environment is higher (dev-server `--reload`, single worker, no
connection pooling) — re-measure against a production ASGI deployment before
using round-trip time as an SLA figure. See PRICING_ENGINE.md §6 for the
same caveat in more detail.

## 6. Error formats

Every error response — from the new auth/rate-limit layer, from an existing
route's `HTTPException`, or from an unexpected server exception — is
normalized to one JSON shape by a global exception handler:

```json
{"error_code": "AUTHENTICATION_ERROR", "message": "Invalid API key.", "request_id": "b1e2...", "details": null}
```

| `error_code` | HTTP status | Meaning |
|---|---|---|
| `AUTHENTICATION_ERROR` | 401 | Missing/invalid/revoked key, or disabled consumer |
| `RATE_LIMIT_ERROR` | 429 | Per-minute or daily quota exceeded; `details.retry_after_seconds` and `details.reason` are set |
| `VALIDATION_ERROR` | 400/422 | Bad request shape, including FastAPI's own request-validation errors |
| `NOT_FOUND` | 404 | e.g. match not found |
| `MODEL_UNAVAILABLE` | 503 | Model/context not yet trained or available |
| `DATA_UNAVAILABLE` | 503 | Required underlying data missing |
| `INTERNAL_ERROR` | 500 | Unexpected server error — client-visible message is intentionally generic; full detail is server-side logged against `request_id` |

`request_id` in the body always matches the response's `X-Request-ID`
header — quote it when reporting an issue. Pattern-match on `error_code`,
not on `message` text, since wording may change.

## 7. Model-version semantics

Every pricing response carries a `provenance` block:

```json
{"model_name": "disposal_nb", "model_version": "disposals_huber@2026-08-23T12:40:50.407355", "generated_at": "...", "data_cutoff": "..."}
```

- `model_version` is `<model_name>@<run_at ISO timestamp>` of the
  **currently-promoted** model run — it changes only when a new model is
  promoted (see `/api/v1/model-registry` for the full promotion history and
  audit trail), never silently mid-round.
- `generated_at` is when this specific response/projection was computed;
  `data_cutoff` is the latest match data that projection is allowed to have
  seen (leakage-safe point-in-time cutoff).
- A promotion event is permanent and auditable — the previous champion's
  historical predictions and metrics are preserved, never deleted, so
  historical `model_version` values in old data remain meaningful.

## 8. Data freshness semantics

Player-market responses (`disposals`, `goals`) additionally carry:

- `lineup_status`: `"confirmed"` or `"uncertain"` — whether the player's
  selection for this match is locked in.
- `is_stale` / `stale_reasons`: `true` when the projection was generated
  before a materially newer signal arrived (e.g. a lineup change since
  `generated_at`) — always check this before treating a cached-looking
  response as current.

Service-level freshness (not per-projection) is available from
`GET /api/v1/integration-health`: last successful fixture refresh, last
successful odds refresh, and stale-data warnings if either has gone quiet
longer than expected. See §9.

## 9. Integration health endpoint

`GET /api/v1/integration-health` — poll this for monitoring/alerting rather
than inferring health from pricing response latency:

```json
{
  "status": "ok",
  "generated_at": "2026-08-23T13:10:48.015909+00:00",
  "last_fixture_refresh": "2026-08-23T07:26:52.589453+00:00",
  "last_odds_refresh": "2026-08-23T07:26:52.589453+00:00",
  "current_round": 24,
  "current_season_year": 2026,
  "promoted_models": {
    "player_disposals": "disposals_huber@2026-08-23T12:40:50.407355",
    "player_goals": "goals_hurdle@2026-08-16T04:52:52.559756",
    "team_elo": "elo@2026-08-14T14:04:30.922530",
    "team_poisson": "poisson@2026-08-15T07:06:27.617657"
  },
  "stale_warnings": []
}
```

`status` is `"degraded"` whenever `stale_warnings` is non-empty (e.g. no
successful refresh in the expected window, or a market has no promoted
model) — treat `"degraded"` as "investigate," not necessarily "down."

## 10. Read-only guarantee

Every endpoint documented here is read-only — no request body, no mutation
of pricing/model state. Nothing in this API surface places, records, or
implies a stake.

## 11. Versioning

`v1` is the only version, and it means what's documented on this page today
— there is no `v2` in development and no deprecation schedule to plan
around. A breaking change to a `v1` response shape would ship as a new
prefix rather than silently altering `v1`; additive fields (new optional
response keys) do not bump the version.

## 12. Provisioning a consumer (operator-side)

Consumer and key lifecycle is CLI-only — there is no HTTP admin endpoint,
deliberately, since an HTTP key-management surface would need its own auth
story:

```bash
python -m app.api_platform.cli create-consumer --name "Acme Sportsbook" --rate-limit-per-minute 60 --daily-quota 5000
python -m app.api_platform.cli create-key --consumer "Acme Sportsbook"    # prints the raw key once — store it now
python -m app.api_platform.cli usage --consumer "Acme Sportsbook"         # request counts, success rate, p50/p95 latency
python -m app.api_platform.cli revoke-key --key-prefix afl_XXXXXXXXXXXX
python -m app.api_platform.cli disable-consumer --name "Acme Sportsbook"
```

## 13. Minimal client example

[`examples/b2b_client_example.py`](../../examples/b2b_client_example.py) is
a dependency-free (stdlib `urllib` only) script that authenticates, requests
match/player/SGM pricing, prints provenance, and handles an API error —
useful as a starting point independent of language/framework choice.
