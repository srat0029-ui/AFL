# Deployment

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system diagram and
[OPERATIONS.md](OPERATIONS.md) for health checks, logs, backups, and
failure behavior. This page covers building, migrating, and deploying the
system.

## Local development

No Docker needed for day-to-day development — see the root
[README.md](../README.md) for the plain `uvicorn`/`vite` workflow against
SQLite.

## Local development against Postgres (Docker Compose)

The dev default is SQLite (zero setup). To exercise the real production
database engine locally:

```bash
docker compose up -d --build
docker compose exec backend python -m alembic upgrade head
curl http://localhost:8000/api/health/db
```

This starts a `postgres:16-alpine` container (host port `5433`, to avoid
clashing with any other local Postgres) and the backend container built
from `backend/Dockerfile`, wired together via `DATABASE_URL`. Tear down
with `docker compose down` (add `-v` to also delete the Postgres volume).

## Building the backend image directly

```bash
cd backend
docker build \
  --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t afl-backend .
```

The image is `python:3.13-slim`, non-root (`appuser`), no dev/test
dependencies (see `requirements.txt` vs `requirements-dev.txt`), no
`--reload`, and runs `uvicorn app.main:app --workers 2`. It's ~990MB —
xgboost's Linux wheel pulls in a ~250MB GPU (NCCl) dependency this project
never uses on CPU-only training/inference, removed in the same Docker
layer it's installed in (see the Dockerfile comment).

## Deployment target: Render

Evaluated against what this project actually needs — a Docker web service,
a background worker for the live-cycle scheduler, managed Postgres, and
static frontend hosting — Render is the only platform that covers all four
under one account/secret model without Kubernetes or hand-rolled cloud
wiring. `render.yaml` (repo root) is a Render Blueprint covering all four
services as code.

**This blueprint has not been verified against a live Render account** — no
credentials were available while building this phase. Review it before
first use rather than assuming it deploys unmodified.

To provision for real:

1. Push this repo to GitHub (already done) and connect it in the Render
   dashboard as a new Blueprint, pointing at `render.yaml`.
2. Render creates: `afl-postgres` (managed Postgres), `afl-backend` (web
   service, Docker, health check `/api/health`), `afl-live-cycle-scheduler`
   (background worker running the scheduler), `afl-frontend` (static site).
3. Fill in the `sync: false` env vars Render will prompt for:
   - `afl-backend` / `afl-live-cycle-scheduler`: `CORS_ORIGINS` (the real
     `afl-frontend` URL once known), `THE_ODDS_API_KEY` (optional).
   - `afl-frontend`: `VITE_API_BASE_URL` (the real `afl-backend` URL).
4. Run the migration step (see below) before the web service serves
   traffic for the first time.
5. Run `python scripts/smoke_test.py --base-url <afl-backend URL> --frontend-url <afl-frontend URL>` against the real deployment.

## Database migrations in production

**Migrations are an explicit, separate step — the application never
auto-migrates on startup.** Two workers booting concurrently and both
attempting `alembic upgrade head` is a real race; a human/CI-triggered
migration step run once, before the new image is promoted to serve
traffic, avoids it entirely. This also means a migration failure blocks
the release cleanly (non-zero exit) rather than partially applying while
the app is already serving requests.

To run migrations against production from a operator machine or CI (never
from inside the running web service):

```bash
DATABASE_URL=<production postgres URL> python -m alembic upgrade head
```

or, using the built container image directly:

```bash
docker run --rm -e DATABASE_URL=<production postgres URL> ghcr.io/<repo>/backend:latest python -m alembic upgrade head
```

If a migration fails: the release does not proceed, the previous image
keeps serving traffic (nothing was torn down), and the failure is visible
in the CI/operator output with a real Python traceback — investigate and
fix forward, or roll back the specific migration if it's clearly wrong.
**Nothing about this process ever drops/recreates a production table
automatically** — every migration in `alembic/versions/` is additive or
explicitly, deliberately written (see `8316cece5ae7`'s venue-consolidation
migration for an example of an intentional, reviewed data change).

Verified in this phase: `alembic upgrade head` from an empty Postgres
database (via Docker Compose) applies all 38 migrations cleanly, and
upgrading from a representative earlier revision (`1817ff671b79`, roughly
the pre-Trading-Monitor/API-platform era) to head also succeeds. Fixed two
real SQLite-only assumptions this surfaced (see OPERATIONS.md's changelog
note) that would otherwise have broken a first-time Postgres migration.

## CI/CD flow

- **Pull request**: `.github/workflows/ci.yml` runs backend tests + lint +
  migration checks, a Postgres-integration subset, frontend
  tests/lint/build, and a Docker build-sanity check. Nothing is deployed.
- **Push to `master`**: the same checks, plus building and pushing a
  versioned image to GHCR (`ghcr.io/<repo>/backend:sha-<short-sha>` and
  `:latest`) using the built-in `GITHUB_TOKEN` — no extra secret needed for
  this part.
- **Promotion to production**: currently a deliberate manual step (run the
  migration command above, then redeploy the `afl-backend`/
  `afl-live-cycle-scheduler` services on Render to the new image tag).
  There's no staging environment yet, so PR-time CI is the pre-production
  gate rather than a separate hosted staging tier.

### Enabling continuous deployment

The `deploy` job in `ci.yml` already has a real (currently inert) step
that `curl`s a Render deploy hook. To turn on auto-deploy after a
successful `master` build:

1. In Render, create a deploy hook URL for `afl-backend` (and repeat for
   the worker service if desired).
2. Add it as a GitHub Actions repository secret named
   `RENDER_DEPLOY_HOOK_URL`.

Once set, every green `master` build triggers a deploy automatically. Note
this still doesn't run migrations for you — Render's own "pre-deploy
command" setting (or a manual step immediately before) should point at the
migration command above.

## Rollback

Redeploy the previous image tag (`ghcr.io/<repo>/backend:sha-<previous
sha>`) on Render. Every migration in this project is additive (no column
drops without an explicit, reviewed migration), so rolling back the
application code while the schema stays at the newer revision is safe —
the older code simply doesn't read the newer columns.
