# AFL Analytics & Betting Insights Platform

Statistically grounded AFL match analysis: model probabilities compared against
sportsbook odds to surface potentially mispriced markets, with honest
uncertainty and no guarantees. Architecture is sport-agnostic; AFL is the
first (and for now, only) sport implemented.

## Status

**Stage 0 — foundation only.** No modelling, scraping, or odds logic yet. What
exists: a FastAPI backend with a SQLite database (Postgres-ready), a
React + TypeScript frontend, core schema (teams/venues/seasons/rounds/matches),
provider interfaces for future data sources, and a working
frontend → API → database round trip.

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
