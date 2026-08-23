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

**Not implemented yet — this is a dev/demo build with no auth layer.** For a
production integration, the intended approach is a per-consumer API key sent
as a `X-API-Key` header, validated by a FastAPI dependency ahead of every
router (the same `Depends(get_db)` pattern already used for the DB session
would extend naturally to `Depends(get_api_key)`). No timeline is committed —
this section states intent, not a shipped feature.

## 4. Rate limiting

**Not implemented yet.** Recommended strategy for production: a per-key
token-bucket limit at the reverse-proxy/gateway layer (not in application
code), since pricing responses are cheap to serve from cache (§5) and the
actual cost driver is protecting the upstream odds-provider integration, not
this API's own compute.

## 5. Expected response times

Measured in-process (excludes HTTP/dev-server overhead) via
[`scripts/benchmark_pricing.py`](../scripts/benchmark_pricing.py) against the
real current-round dataset. Re-run it any time — it's read-only:

```bash
python -m scripts.benchmark_pricing
```

| Operation | Typical | Notes |
|---|---|---|
| Single team-market pricing | ~1–2ms | Elo/Poisson context is pre-fit, not re-fit per request |
| Full round pricing, cache miss | ~100ms | scales with matches × players in the round |
| Full round pricing, cache hit | <1ms | 30s TTL cache around the whole round response |
| Arbitrary player-threshold query | ~1ms | evaluates one persisted distribution at any threshold |

These are in-process compute figures. Observed HTTP round-trip time in this
dev environment is higher (dev-server `--reload`, single worker, no
connection pooling) — re-measure against a production ASGI deployment before
using round-trip time as an SLA figure. See PRICING_ENGINE.md §6 for the
same caveat in more detail.

## 6. Error formats

Standard FastAPI/Pydantic conventions, no custom envelope:

- **Domain errors** (not found, unsupported combination): `HTTPException`,
  e.g. `{"detail": "match not found"}` with `404`; `{"detail": "unsupported
  market_type/selection combination"}` with `400`.
- **Model/context unavailable** (e.g. team models not yet trained):
  `503` with `{"detail": "<reason>"}`.
- **Request validation errors** (bad query param type, missing required
  param): FastAPI's standard `422` with a `{"detail": [{"loc": [...], "msg":
  ..., "type": ...}]}` array — one entry per invalid field.

There is currently no distinct error code/taxonomy beyond the HTTP status
and `detail` string — do not pattern-match on `detail` text for control flow
without confirming it first via `/docs`, since message wording may change.

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
