# Multi-sport architecture (AFL + NBA)

Status: **NBA foundation plus a historical data pipeline.** The NBA data
model, information boundary and prospective pipeline exist and are tested.
Teams, schedule, results and player box scores are ingested from ESPN, with
a validation report over what is stored; the local development database
holds 2015-16 through 2025-26 plus the 2026-27 schedule (see
[NBA_DATA_SOURCES.md](NBA_DATA_SOURCES.md) for the source, its limits and
the validation results). There is **no odds or injury ingestion, no NBA
model, no recommendations and no live cycle yet**. Nothing in the NBA code
fabricates data; the `/nba` page shows only real row counts.

This document records what is shared between the sports, what is not, and
why. It supersedes the parts of [NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md)
noted under "Where this departs from the earlier plan" below.

## The research question NBA is built around

> Can our model identify mispriced NBA player props prospectively and
> consistently beat the closing market?

Scope follows from it: player-prop **singles** only — points, rebounds,
assists — across multiple bookmakers, evaluated by closing-line value and
calibration. No same-game multis, no team markets, no multi builder.

## Layout

```
backend/app/
  core/                    shared, sport-agnostic (new)
    settlement.py            result vocabulary + "did it clear the line"
    prospective.py           information-cutoff check + write-once ORM guard
    clv.py                   closing-line-value arithmetic
  edges/overround.py       shared pure maths, used in place by both sports
  edges/fair_odds.py         "
  modelling/metrics.py       " (Brier, log loss, calibration table, ECE)
  modelling/bootstrap.py     "
  providers/the_odds_api.py  shared odds client (sport key + region per sport)
  providers/afl/             AFL providers (+ AFL's odds market keys)
  providers/nba/             NBA stats provider (ESPN) + its record shapes
  api_platform/              shared (API keys, rate limiting, errors)

  models/                  AFL tables (+ shared `bookmakers`, `sports`)
  models/nba/              NBA tables (new)
  nba/                     NBA domain logic (new)
    markets.py               the three markets, their stat, provider keys
    projection.py            model contract: minutes x per-minute rate
    asof.py                  THE information boundary
    prospective.py           project -> freeze -> close -> settle
    ingestion.py             teams, games, players, game logs (idempotent, resumable)
    validation.py            data-quality report over the stored history
    cli.py                   backfill / sync-* / validate
    status.py                what data exists
  api/routes/nba.py        /api/nba/*
  player_modelling/, modelling/, pricing/, market_monitor/,
  trading_monitor/, ingestion/      AFL — unchanged

frontend/src/
  pages/nba/               NBA pages, under the /nba route namespace
  components/ui/           shared design system, unchanged
```

Three tests in `backend/tests/test_nba_foundation.py` hold these boundaries
in place mechanically: NBA and `core` code may not import AFL packages,
`core` may not import any sport, and NBA tables may only hold foreign keys to
other NBA tables or `bookmakers`.

## What is shared

| Shared thing | How |
| --- | --- |
| `bookmakers` table, incl. exchange/eligibility flags | NBA quotes and predictions reference it directly. One bookmaker, one row, whichever sport. |
| De-vig, fair odds, expected value | `app/edges/overround.py`, `app/edges/fair_odds.py` — already pure; imported in place. |
| Line settlement arithmetic and result vocabulary | `app/core/settlement.py`. Lifted out of AFL's `prop_settlement.py`, which now delegates to it. |
| Calibration and scoring metrics, bootstrap CIs | `app/modelling/metrics.py`, `bootstrap.py`, `calibration_methods.py` — already pure; imported in place. |
| Odds provider client | `app/providers/the_odds_api.py`. Same request/response shape for every sport; only the sport key and default region differ. |
| Provider DTOs | `app/providers/types.py` (`ProviderEvent`, `PlayerPropQuote`, ...). |
| Prospective-integrity primitives | `app/core/prospective.py` (new). |
| CLV arithmetic | `app/core/clv.py` (new; AFL does not use it yet). |
| API platform, DB session, config, logging | Unchanged. |
| Frontend design system and nav shell | `components/ui/*`, `App.tsx`. |

## What is deliberately not shared

**Domain tables.** NBA has its own `nba_*` tables rather than adding rows to
AFL's `matches` / `players` / `teams` / `player_prop_markets` /
`pricing_snapshots`. See the next section for why.

**Everything that encodes a sport's rules** stays in that sport's package:
AFL's Elo/Poisson team models, disposal/goal models, hurdle and NB2
distributions, round and finals handling, lineup-selection vocabulary,
teammate/opponent context, SGM dependence, the Multi Builder, weekly
shortlist, market monitor, trading monitor, and AFL's live cycle. None of it
was generalised, and none of it was changed.

**Model run / promotion registry, live-cycle run history, market-movement
analysis, model-vs-market reporting.** These are shared in *shape* but the
AFL implementations are bound to AFL tables. NBA will write its own next to
them when it has something to record; lifting a shared version is only worth
doing once two real implementations visibly duplicate each other.

## Where this departs from the earlier plan

NBA_PLATFORM_PLAN.md (sections B, D, N) proposed storing NBA as rows with a
different `sport_id` in the existing tables, on the basis that `sport_id`
"already exists project-wide". Reading the code showed that is not safe:

- `sport_id` exists on the tables, but **39 AFL modules query
  `Match` / `Player` / `Team` / quotes with no sport filter.** Six of them
  sweep every `SCHEDULED` match — including `pricing/snapshot_service.py`,
  `pricing/sgm_snapshot_service.py`, `market_monitor/*` and
  `player_modelling/model_value_observations.py`. An NBA game in `matches`
  would be picked up and priced by AFL's Elo/Poisson pipeline.
- The shapes differ where it matters. `Match.round_id` is `NOT NULL` and the
  table carries goals/behinds; `PlayerMatchStat` is AFL's stat columns;
  `PropMarketObservation` has no closing-line fields and assumes only the
  "over" side is ever observed.

Making the shared tables safe would mean adding sport filters across those
39 modules — a large, risky change to a frozen AFL v1, for no benefit to AFL.
Separate NBA tables give complete isolation at zero AFL risk. The cost is
that table-bound helpers are not reused; the pure maths underneath them is.

`sports` rows are not needed for NBA (an `nba_*` table is NBA by
construction), so no `Sport(code="NBA")` row is inserted.

## NBA data model

| Table | Kind | Notes |
| --- | --- | --- |
| `nba_teams`, `nba_players` | reference | Team identity is the provider's team id; player identity is `(source, source_player_id)`. Never the name. |
| `nba_games` | reference/result | `scheduled_start` (UTC tip-off) is the prospective boundary; `game_date` is the league's local date. No rounds. Rest days, back-to-backs, home/away and opponent are **derived** from this table as of a cutoff, never stored. |
| `nba_player_game_logs` | result, corrected in place | Minutes and `started` are first-class; minutes are whole numbers as the source publishes them. A did-not-play row is distinct from no row (box score not ingested). Stored only if the player points add up to the team score. |
| `nba_schedule_sync_dates` | bookkeeping | Which dates the schedule sync has fetched; makes a backfill resumable. Not read for modelling. |
| `nba_player_availability_reports` | append-only | `observed_at` (when we fetched it) is separate from `source_published_at`. `source_status` is the raw status; canonical `status` is empty when there is no safe mapping. |
| `nba_prop_quotes` | append-only | One row per bookmaker / line / side / observation. Both sides stored so the book can be de-vigged. `is_alternate_line` separates the main line from the ladder. This table is the market-movement history and the source of the closing line. |
| `nba_prop_projections` | append-only, frozen | Stores `expected_minutes`, `rate_per_minute`, distribution parameters and `information_cutoff`. A re-projection is a new row. |
| `nba_prop_predictions` | frozen at entry; closing and settlement each written once | The prospective evidence record. |

Not yet modelled as tables, because no real source has been chosen and the
shape should not be guessed: pre-game starting-lineup announcements, team
pace/usage aggregates, and any model-run/promotion registry for NBA.

## Prospective integrity

Four kinds of knowledge are kept physically separate:

| Knowledge | Where it lives | When it is written |
| --- | --- | --- |
| Information available at prediction time | `nba_prop_projections` (`information_cutoff`, `inputs`) | Before tip-off |
| Entry line and odds | `nba_prop_predictions.entry_*` | Before tip-off, at insert |
| Closing line and odds | `nba_prop_predictions.closing_*` | Once, after tip-off |
| Actual outcome | `nba_prop_predictions` settlement columns | Once, after the game is final |

Three mechanisms enforce it, each tested:

1. **One read path for history** — `app/nba/asof.py`. Every function takes a
   cutoff. Quotes and availability reports are filtered on `observed_at`; a
   box score counts as known only when the game is final and tipped off at
   least four hours before the cutoff. Live prediction and backtesting use
   the same functions, so a backtest cannot quietly use a looser read path
   than production.
2. **Checks at each write** — `app/nba/prospective.py`. A projection or
   prediction is rejected at or after tip-off, or if its information cutoff
   is in the future. A closing line cannot be captured before tip-off and
   only reads quotes observed at or before it. Settlement waits for a final
   game and an ingested box score; a player who did not play is void.
3. **A write-once guard at the ORM layer** — `app/core/prospective.py`.
   Frozen columns cannot change after insert, the closing group and the
   settlement group can each be written once, and protected rows cannot be
   deleted. This is new: AFL upholds the same rules by discipline in each
   write path.

Known limits, stated plainly:

- The guard covers ORM flushes. A bulk `update()`/`delete()` statement or raw
  SQL bypasses it. Database triggers on Postgres would close that gap.
- AFL's existing tables are not retrofitted with the guard.
- `nba_player_game_logs` is corrected in place, so a backtest run after a
  stat correction sees the corrected value. Settlement is unaffected (it
  copies the value it settled on).

## Open design points for the next tasks

- **Withdrawn quotes.** Ingestion is expected to write a quote row only when
  a price changes, so "latest quote" cannot distinguish an unchanged price
  from an alternate line the bookmaker has pulled. A superseded *main* line
  is already handled. The ingestion task needs a per-fetch marker (or
  equivalent); until then callers can pass `max_quote_age`.
- **Evaluation unit.** One prediction is frozen per (projection, line, side).
  A later projection can freeze another for the same line. Evaluation must
  pick its unit (e.g. earliest prediction per game/player/market/line)
  rather than count every row.
- **Recommendations.** No policy exists for which predictions are worth
  betting. When one does, its verdict must be frozen on the row at entry
  time, not applied afterwards.
- **Bookmaker region.** The odds provider documents NBA player props as
  "mainly limited to US sports and US bookmakers", so the client defaults
  NBA to the `us` region. Whether Australian books return NBA props has not
  been tested against a live key.
- **Stats source.** ESPN's public site API is in use for schedule and box
  scores. It is unofficial; see NBA_DATA_SOURCES.md for what that means and
  what else was tested.
