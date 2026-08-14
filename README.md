# AFL Analytics & Betting Insights Platform

Statistically grounded AFL match analysis: model probabilities compared against
sportsbook odds to surface potentially mispriced markets, with honest
uncertainty and no guarantees. Architecture is sport-agnostic; AFL is the
first (and for now, only) sport implemented.

## Status

**Stage 1 in progress.** Stage 0 (foundation) is done: FastAPI backend with a
SQLite database (Postgres-ready), React + TypeScript frontend, core schema
(teams/venues/seasons/rounds/matches), provider interfaces, and a working
frontend → API → database round trip.

**Stage 1.1 (data ingestion) is done:** a `FixtureProvider` implementation
backed by the free [Squiggle API](https://api.squiggle.com.au/), idempotent
ingestion into the core schema, and a CLI to backfill historical seasons and
pull upcoming fixtures. The dev database currently holds the 10 most recent
completed AFL seasons (2016–2025) plus upcoming 2026 fixtures — 2,070 real
matches, 18 teams, 31 venues.

**Stage 1.2 (Elo model) is done:** a margin-of-victory-adjusted Elo rating
engine with season-carryover regression, walk-forward validated with no data
leakage (each prediction uses only strictly-earlier matches). Hyperparameters
were selected on a 2016–2022 tune window and validated on a genuinely
held-out 2023–2025 window: **Brier score 0.2012, accuracy 68.1%** on data the
tuning process never saw, with tight calibration (predicted probability
buckets track actual outcome rates closely). Ratings are persisted to the
`elo_ratings` table.

**Stage 1.3 (Poisson scoring model) is done:** each team's score is modelled
as 6×Goals + Behinds (independent Poisson processes), not raw points
directly — a raw-points Poisson would have unrealistically narrow variance
(std≈9.5 vs AFL's real ~20-25). Team attack/defense strength is a rolling-window
ratio model; home-ground advantage is derived from data (separate rolling
home/away league averages), not a guessed multiplier — an earlier guessed-multiplier
version was caught double-counting home advantage and inflating predicted
totals by a test, and was redesigned. Validated the same tune/holdout way as
Elo: on the 2023–2025 holdout window, **margin predictions beat a naive
"always guess the average" baseline by 13%** (correlation 0.54) and win
probability is comparable to Elo's (Brier 0.2052 vs Elo's 0.2012) — but
**total-points predictions show no clear edge over the naive baseline yet**
(MAE 23.47 vs naive 23.52). That's reported honestly rather than hidden: team
attack/defense ratios alone capture relative strength (winner/margin) well,
but total-points/pace likely needs richer features (Stage 2) to move beyond
a trivial baseline. Predictions persist to `poisson_match_predictions`.

**Stage 1.4 (manual odds entry) is done:** `bookmakers`/`odds_quotes` tables
(market_type kept as an open-ended string, not a fixed enum, so new markets
never need a migration), a `ManualOddsProvider` implementing the `OddsProvider`
interface from Stage 0 (so Stage 1.5's edge calculation can query odds
uniformly regardless of source later), REST endpoints with real validation
(selection must match an actual team for h2h/line, must be over/under for
totals, price must be valid decimal odds >1.0), and a frontend page — with
routing introduced for the first time — to browse upcoming fixtures and log
h2h/line/total prices. Verified end-to-end in-browser against the real API
and database, including via `ManualOddsProvider` reading the entered quotes
back correctly.

**Stage 1.5 (fair odds & edge calculation) is done:** the core insight the
platform exists to produce. Overround removal (proportional de-vig, matching
a bookmaker's own two-sided quotes rather than mixing prices across
bookmakers), fair odds, expected value, and a documented sign convention for
handicap lines. Predictions are computed live per request (walk-forward is
well under a second over the whole history) rather than persisted, avoiding
a staleness problem for upcoming-match forecasts. Two distinct axes: **edge
tier** (strong/moderate/weak/none, from the raw probability gap) and
**confidence tier** (higher/moderate/lower/insufficient_data) — the latter
hard-gated by each model's own validated performance per market (a new
`model_runs`/`model_validation_metrics` pair the CLIs now persist), so
Poisson's total-points market — which Stage 1.3 found has no real edge over
naive — is automatically capped at "insufficient data" no matter how large
its raw edge number looks, rather than needing that fact hand-maintained
somewhere. h2h uses Elo as the primary model with Poisson's probability as a
secondary cross-check feeding the model-agreement confidence factor (not
blended into one number). Edges show inline on the odds entry page. Verified
end-to-end with real data: de-vigging a two-sided Fremantle/Adelaide quote
correctly surfaced a genuine value finding on the de-vigged underdog price,
and a total-points quote correctly showed "Insufficient data" regardless of
its raw numbers.

**Stage 1.6 (dashboard & match detail page) is done:** the MVP's "clean
dashboard" — an upcoming-matches grid with team-coloured win-probability bars
and a best-edge badge per match (ranked by confidence first, raw edge size
second, so a low-confidence "strong" edge never outranks a genuinely
trustworthy smaller one — see `best_edge()`), and a match detail page
combining the model-probabilities view with the odds/edges panel from Stage
1.5, moved here from its old standalone picker page since it belongs on the
match it's about. Two new endpoints support this: `GET /api/matches/{id}/predictions`
(model view for a match regardless of whether odds exist yet — closed a real
gap, since `/edges` only ever returned data for matches with odds already
recorded) and `GET /api/dashboard` (all upcoming matches in one response,
building the walk-forward model state once and reusing it, rather than once
per match). Verified end-to-end in-browser: added a quote on the match detail
page and confirmed it immediately appeared as the dashboard's best-edge badge
on that match's card. The full 13-section match page from the original brief
(weather, player availability, H2H charts, quarter-by-quarter trends) needs
data this project doesn't have yet — that's Stage 3 territory, not part of
the MVP.

**Stage 1.7 (backtesting engine) is done:** a `/backtest` page giving full
visibility into how both models actually perform, split into two genuinely
different things that shouldn't be conflated. First, statistical validation —
Brier score, log loss, accuracy, and a 10-bucket calibration table for both
Elo and Poisson's win-probability output, each broken down by season, by
team, and by the model's own conviction level (not the same axis as the live
confidence tiers on the dashboard, which also factor in market data this
backtest doesn't use), plus Poisson's total-points/margin MAE by season. This
needs no odds data — it's recomputed live from each `ModelRun`'s persisted
config via the same walk-forward functions Stage 1.2/1.3 already built, so it
reflects the current model exactly, not a stale snapshot. Second, logged-odds
performance — real win rate, ROI, and P&L, but computed *only* from odds
you've actually recorded on fixtures that have since resolved (via the
Stage 1.4 odds-entry flow), never from historical market data, because no
free source of that exists for AFL and fabricating one would defeat the
point of this whole project. That section starts empty and fills in as
logged quotes resolve — verified this by logging a real quote against an
already-completed match (Richmond @ 1.85 h2h, which they won), confirming
the report correctly showed it as a resolved win with `pnl_units = 0.85`
and `roi_pct = 85%`, then deleting the test quote to leave the real database
untouched. Correctly handles the AFL rule that drawn h2h bets are void
(refunded), not losses. All 2,052 completed matches across the 10 loaded
seasons run through both reports in well under a second — no caching
needed.

No player markets or a standing bet-tracking dashboard yet — those are later
Stage 1 sub-stages and beyond.

See `backend/` and `frontend/` for their own setup notes below.

## Project layout

```
AFL/
  backend/    FastAPI + SQLAlchemy + Alembic (Python analytics/API layer)
  frontend/   React + TypeScript (Vite)
  .claude/launch.json   dev-server definitions (backend on :8000, frontend on :5173)
```

## Local development

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --reload
```

API runs at http://localhost:8000. Health checks: `/api/health`, `/api/health/db`.

Run tests: `.venv\Scripts\python -m pytest -v`

### Data ingestion

Backfill historical seasons and/or pull upcoming fixtures from Squiggle (safe
to re-run — idempotent, updates in place rather than duplicating):

```bash
.venv\Scripts\python -m app.ingestion.cli --seasons 2016 2025
.venv\Scripts\python -m app.ingestion.cli --upcoming
```

Requires `curl` on PATH (bundled with Windows 10+ and most Linux/macOS
installs) — see the note at the top of `app/providers/afl/squiggle.py` for why
the provider shells out to curl instead of using an in-process HTTP client.

### Modelling

Tune, validate, and persist the models (safe to re-run — wholesale recompute,
not additive):

```bash
.venv\Scripts\python -m app.modelling.elo_cli
.venv\Scripts\python -m app.modelling.poisson_cli
```

Each prints tune-window vs holdout-window validation metrics (with a naive-baseline
comparison for the Poisson model's total-points/margin numbers), a calibration
table, then a sanity-check preview of upcoming fixture predictions. See the
module docstrings in `app/modelling/` for the walk-forward, tune/holdout-split,
and home-advantage-derivation methodology.

**Both CLIs must be run at least once** before edges can be computed — they
persist each model's config and per-market validation results
(`model_runs`/`model_validation_metrics`), which `GET /api/matches/{id}/edges`
reads to decide confidence tiers. Without that, the endpoint returns 503.

### Frontend

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

App runs at http://localhost:5173. Routes: `/` (dashboard — upcoming matches
with model win probability and best edge), `/matches/:id` (match detail —
model probabilities, odds entry, edges), `/backtest` (statistical validation
and logged-odds performance for both models), `/status` (backend/DB
connectivity check from Stage 0).

### Database

SQLite for local development (`backend/afl.db`, gitignored) — zero setup.
Schema is managed by Alembic migrations under `backend/alembic/versions/`, so
moving to Postgres later only requires changing `DATABASE_URL` in `.env` and
installing `psycopg2-binary`; no application code changes.
