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

No odds, edge calculation, or dashboard yet — that's the rest of Stage 1.

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

Tune, validate, and persist the Elo match-winner model (safe to re-run —
wholesale recompute, not additive):

```bash
.venv\Scripts\python -m app.modelling.cli
```

Prints tune-window vs holdout-window Brier score / log loss / accuracy /
calibration, then a sanity-check preview of upcoming fixture probabilities.
See the module docstrings in `app/modelling/` for the walk-forward and
tune/holdout-split methodology.

### Frontend

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

App runs at http://localhost:5173.

### Database

SQLite for local development (`backend/afl.db`, gitignored) — zero setup.
Schema is managed by Alembic migrations under `backend/alembic/versions/`, so
moving to Postgres later only requires changing `DATABASE_URL` in `.env` and
installing `psycopg2-binary`; no application code changes.
