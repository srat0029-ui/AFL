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

This starts a `postgres:18-alpine` container (host port `5433`, to avoid
clashing with any other local Postgres) — matching the real production
target, see "PostgreSQL version" below — and the backend container built
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

## $0 architecture

Hard constraint for this deployment: **the whole system must cost $0 and
contain nothing capable of silently generating a charge.** This ruled out
two pieces of the originally-evaluated Render Blueprint:

- **Render background workers have no free plan at all** — confirmed
  directly from Render's docs, not assumed. The live-cycle scheduler is
  therefore not a Render service at all; it's a GitHub Actions scheduled
  workflow (`.github/workflows/live-cycle.yml`) invoking one existing
  `run-live-cycle` per trigger, from the same immutable GHCR image the
  backend runs. GitHub Actions on standard runners is free for public
  repositories with no quota to exceed — confirmed from GitHub's own
  billing docs (this applies to both compute minutes and artifact
  storage).
- **Render's free Postgres expires 30 days after creation and has no
  backups at all on that tier.** For a project whose whole point is a
  genuine, undamaged prospective evidence record, that's not an acceptable
  risk regardless of cost. The database is instead an external free-tier
  managed Postgres provider (evaluated: flat $0 tier, no card, no metered
  billing dimension that could silently accrue a charge — see the
  provider comparison this phase's audit produced). A separate weekly
  GitHub Actions workflow (`.github/workflows/backup.yml`) covers what the
  provider's free tier doesn't: an encrypted `pg_dump`, verified and
  uploaded as a 30-day-retention Actions artifact.

What's left is genuinely two Render services, both Free: `afl-backend`
(web service) and `afl-frontend` (static site). See
[OPERATIONS.md](OPERATIONS.md) for the full cost/limitation table.

## Deployment target: Render (backend + frontend only)

`render.yaml` (repo root) now covers exactly two services — the worker and
the `databases:` block that used to define Render Postgres are both gone.

**This blueprint has not been verified against a live Render account** — no
credentials were available while building this phase. Review it before
first use rather than assuming it deploys unmodified.

The backend deploys via `runtime: image`, pulling the exact GHCR image
GitHub CI already built and verified (`image.url`, pinned to a real
`sha-<commit>` tag or `@sha256:...` digest — never `:latest`) rather than
having Render rebuild the Dockerfile itself. This matters for two reasons:
it guarantees Render runs the literal artifact CI tested, and it's the
only way `/api/release` reports a real commit SHA in production — Render's
own Dockerfile build has no way to pass the `GIT_SHA` build arg that CI
does.

### PostgreSQL version

**Production target: PostgreSQL 18.** The evaluated external provider,
Layerbase, only allows a new managed database to be created as PostgreSQL
18 or a 19 beta — confirmed directly against Layerbase's own public,
unauthenticated engine registry (`GET https://cloud.layerbase.dev/v1/engines`,
no account needed):

```json
"defaultVersion": "18",
"supportedVersions": ["15", "16", "17", "18", "19.0.0-beta.1", "19.0.0-beta.3"],
"creatableVersions": ["18", "19.0.0-beta.3"]
```

18 was chosen deliberately over the 19 beta — **this project never
targets or claims support for PostgreSQL 19**, beta or otherwise. Rather
than switch database providers just to keep the originally-assumed 16, 18
is a stable release, so this project's CI and tooling were brought up to
genuinely verify it instead:

- **`ci.yml`'s `postgres-integration` job is now a 2-entry matrix**
  (`postgres-version: [16, 18]`) — the already-proven 16 baseline is
  retained, and 18 is the actual production target, both run for real on
  every PR/push. 17 and 19 are deliberately not included; this is "keep
  the proven baseline, verify the real target," not general version
  coverage.
- **`docker-compose.yml`'s local Postgres is now `postgres:18-alpine`**
  — production-parity local development matches the real production
  major. (There's no separate test-only Compose file that needs to stay
  on 16; `ci.yml`'s matrix is the only place 16 is still exercised.)
- **`backup.yml`'s `pg_dump`/`pg_restore` client is now
  `postgres:18-alpine`**, matching the real server major.

**Verified for real, not assumed compatible**, against a genuine
`postgres:18-alpine` container: `alembic upgrade head` from empty (38
migrations, including the SQLAlchemy-Core venue-consolidation data
migration) and from the representative `1817ff671b79` revision both
succeed; the full curated Postgres-integration test subset (113 tests —
`PricingSnapshot`, `SgmPriceSnapshot`, settlement, live-cycle idempotency,
API rate limiting) passes unchanged; and a complete backup/restore drill
(migrate → insert a marker row → `pg_dump` with the `postgres:18-alpine`
client → `gpg` encrypt → decrypt → `pg_restore --list` → restore into a
*separate* fresh PostgreSQL 18 database) reproduced all 48 tables and the
marker row exactly, with the dump header confirming `Dumped from database
version: 18.6`.

**Rule for any future major-version change**: never deploy a database
major this project hasn't been integration-tested against in `ci.yml`
first, and keep `backup.yml`'s client image matched to the real server
major — this is exactly the mistake this section was written to correct
once already.

### Activation sequence

The scheduled workflows (`live-cycle.yml`, `backup.yml`) are gated behind
GitHub repository variables (`LIVE_CYCLE_ENABLED`, `BACKUP_ENABLED`) that
default to unset. The gate condition is
`github.event_name == 'workflow_dispatch' || vars.X == 'true'`:
a **scheduled** trigger skips unless the variable is `true`, but a
**manual** `workflow_dispatch` always runs regardless — deliberately, so
the one-off first production run each workflow needs can happen *before*
its recurring schedule is switched on. This lets both workflow files be
committed and merged well before the database and secrets exist, without
generating scheduled failures in the meantime, while still allowing a
controlled manual first run when it's actually time. Provision in this
order:

1. Create the external Postgres database as **PostgreSQL 18** (never the
   19 beta — see above) and run `alembic upgrade head` against its
   **direct** (non-pooled) connection string — schema only, no data
   import (see "Prospective evidence boundary" below).
2. Add `DATABASE_URL`, `THE_ODDS_API_KEY` (optional), and a newly
   generated `BACKUP_ENCRYPTION_PASSPHRASE` as GitHub Actions repository
   **secrets**.
3. Manually trigger `live-cycle.yml` (`workflow_dispatch`) — this runs
   immediately regardless of `LIVE_CYCLE_ENABLED`, as a deliberate,
   watched, one-off check.
4. Confirm the resulting `LiveCycleRun` row and check
   `GET /api/afl/live-status` — this run's timestamp is the production
   prospective-tracking start boundary (see below). Note it down.
5. Set the repository variable `LIVE_CYCLE_ENABLED=true` — the schedule
   now runs unattended every ~15 minutes.
6. Manually trigger `backup.yml` (`workflow_dispatch`) and confirm the run
   succeeds, including its own decrypt/`pg_restore --list` verification
   step.
7. Set `BACKUP_ENABLED=true` to put the backup on its weekly schedule.

Meanwhile, provisioning the Render side can happen independently, in any
order relative to the above:

1. Push this repo to GitHub (already done) and connect it in the Render
   dashboard as a new Blueprint, pointing at `render.yaml`.
2. Render creates: `afl-backend` (Free web service, image-backed, health
   check `/api/health`) and `afl-frontend` (Free static site).
3. Fill in the `sync: false` env vars Render will prompt for:
   - `afl-backend`: `DATABASE_URL` (the external Postgres's direct
     connection string), `CORS_ORIGINS` (the real `afl-frontend` URL once
     known), `THE_ODDS_API_KEY` (optional).
   - `afl-frontend`: `VITE_API_BASE_URL` (the real `afl-backend` URL).
4. Run `python scripts/smoke_test.py --base-url <afl-backend URL> --frontend-url <afl-frontend URL>` against the real deployment.

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

or, using the built container image directly (the same pinned image
`render.yaml` deploys, not `:latest`):

```bash
docker run --rm -e DATABASE_URL=<external postgres direct connection string> \
  ghcr.io/srat0029-ui/afl/backend:sha-4d496ad python -m alembic upgrade head
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
Both migration paths were re-verified for real against a genuine
`postgres:18-alpine` container once 18 became the production target (see
"PostgreSQL version" above) — no incompatibility surfaced.

## Prospective evidence boundary

The external production database is created empty and stays that way
until the first real `run-live-cycle` invocation — **no local development
data is ever imported.** This phase's audit measured the local dev
database and classified every table; only a small "legitimate historical/
model seed" (matches, teams, player match-history, fitted model
coefficients and promotion records — roughly 20MB, not the full ~423MB
local file) is a candidate for a future production import, and that
import has not happened yet.

Explicitly excluded from any future production import, because these
tables hold dev-era data sitting in exactly the columns that must be the
*authoritative* prospective record: `prop_market_observations`,
`pricing_snapshots`, `sgm_price_snapshots` and `sgm_snapshot_legs`,
`model_value_observations`, `live_cycle_runs`, `api_usage_records`, any
dev-era settlement outcomes, `placed_bets` (personal test data), and the
Trading Monitor's dev-era anomaly-case tables. The timestamp of the first
successful production `run-live-cycle` is the production prospective-
tracking start date — everything in those tables from that point on is
real.

## Live-cycle scheduling (GitHub Actions, not a Render worker)

`.github/workflows/live-cycle.yml` runs `python -m app.player_modelling.cli
run-live-cycle` — the existing one-shot command, unchanged — every ~15
minutes (`7,22,37,52 * * * *`, offset from the top of the hour to avoid
GitHub's own scheduler congestion window), from the exact same pinned GHCR
image the backend deploys. A `concurrency: group: live-cycle` block
prevents two runs overlapping if one takes longer than the interval — the
correct equivalent of the old scheduler's file lock for this execution
model, since a file lock is meaningless across ephemeral runner VMs.
`workflow_dispatch` allows a manual trigger at any time.

**Gated**: `if: github.event_name == 'workflow_dispatch' || vars.LIVE_CYCLE_ENABLED == 'true'`
— a scheduled trigger skips until the variable is `true`; a manual
dispatch always runs, on purpose, for the one-off first production run
"Activation sequence" above requires before the schedule is switched on.

Real limitations, documented rather than hidden:

- **Schedule delays**: GitHub's own docs state the `schedule` event can be
  delayed under high platform load, especially at the top of each hour —
  the offset above is that exact mitigation, not a guarantee of exact
  timing.
- **60-day inactivity disables the schedule.** In a public repository,
  GitHub disables scheduled workflows after 60 days with no repository
  activity — and running the scheduled workflow itself does **not** count
  as activity; only new commits do. If this repo goes quiet for that long,
  the live cycle silently stops until someone notices, re-enables the
  workflow (Actions tab → Enable workflow), and triggers it manually.
  Nothing here works around this automatically — that would be exactly
  the kind of extra infrastructure this architecture is deliberately
  avoiding.
- The external database itself may be hibernating between cycles (see
  OPERATIONS.md) — the first query of each cycle may take a few seconds
  longer while it wakes.

## Backups (GitHub Actions, not managed by the database provider)

`.github/workflows/backup.yml` runs weekly (`workflow_dispatch` also
available): `pg_dump -Fc` against the external database, encrypted with
`gpg --symmetric --cipher-algo AES256` **on the runner, before anything is
uploaded**, verified by decrypting and running `pg_restore --list` against
the result (proving the archive is real and intact without ever printing
row data or secrets), then uploaded as a GitHub Actions artifact with
`retention-days: 30`. The plaintext dump and the decrypted verification
copy are both deleted from the runner immediately after use. The
passphrase is piped over stdin (`--passphrase-fd 0`), never passed as a
command-line argument, on both the encrypt and verify steps.

**Gated**: `if: github.event_name == 'workflow_dispatch' || vars.BACKUP_ENABLED == 'true'`
— same mechanism as the live cycle; see "Activation sequence" above.

**This is not managed disaster recovery.** It's a rolling ~30-day window
of encrypted snapshots with no RPO/RTO guarantee — the external provider's
own free tier has no automated backups at all, so this exists to cover
that gap, not to replace a real backup service. Restoring: download the
artifact, `gpg --decrypt`, then `pg_restore` — the same mechanics already
verified for real in this project's local Docker Compose backup/restore
drill (see OPERATIONS.md), just against a different source/destination.

## CI/CD flow

- **Pull request**: `.github/workflows/ci.yml` runs backend tests + lint +
  migration checks, a Postgres-integration subset, frontend
  tests/lint/build, and a Docker build-sanity check. Nothing is deployed.
- **Push to `master`**: the same checks, plus building and pushing a
  versioned image to GHCR (`ghcr.io/srat0029-ui/afl/backend:sha-<short-sha>`
  and `:latest`) using the built-in `GITHUB_TOKEN` — no extra secret
  needed for this part. The same image is what both `afl-backend` and
  `live-cycle.yml` run.
- **Promotion to production**: a deliberate manual step — update
  `render.yaml`'s `image.url` to the new `sha-<commit>` tag (or the
  `@sha256:...` digest) and re-sync the Blueprint, update
  `live-cycle.yml`'s `docker run` image reference to match, then run the
  migration command above before traffic hits the new code. Both files
  should always point at the *same* tag — that's what keeps "one
  immutable artifact, two consumers" true across a promotion, not just at
  the moment this phase wrote them. There's no staging environment, so
  PR-time CI is the pre-production gate rather than a separate hosted
  staging tier.

### Enabling continuous deployment

The `deploy` job in `ci.yml` already has a real (currently inert) step
that `curl`s a Render deploy hook. To turn on auto-deploy after a
successful `master` build:

1. In Render, create a deploy hook URL for `afl-backend`.
2. Add it as a GitHub Actions repository secret named
   `RENDER_DEPLOY_HOOK_URL`.
3. Since `afl-backend` is now image-backed (`runtime: image`), the hook
   call needs an `imgURL` query parameter naming the new tag/digest to
   actually promote it — Render's own deploy-hook docs cover this; a bare
   hook call just re-pulls whatever tag is already configured.

Once set, every green `master` build triggers a deploy automatically. Note
this still doesn't run migrations for you — Render's own "pre-deploy
command" setting (or a manual step immediately before) should point at the
migration command above.

## Rollback

**Backend**: change `render.yaml`'s `image.url` back to the previous
verified `sha-<commit>` tag and re-sync (or use a deploy-hook call with
that tag's `imgURL`). Because it's a pinned digest/tag, this redeploys the
*exact* previously-verified artifact, not a rebuild of an old commit — a
stronger guarantee than a Dockerfile-based rollback would give.

**Frontend**: Render's own per-service rollback (Events page → a previous
successful deploy → Rollback) works unchanged for the static site.

**Migrations**: every migration in this project is additive (no column
drops without an explicit, reviewed migration), so rolling back the
application code while the schema stays at the newer revision is safe —
the older code simply doesn't read the newer columns. No migration in the
current history is irreversible in the sense of destroying data, but none
has a scripted `downgrade()` that's been exercised against real data
either — treat `downgrade()` as unverified if it's ever actually needed.

**If a deploy passes CI but fails the smoke test**: don't debug in
production — roll the backend back to the last known-good tag immediately
(above), then investigate against a local Docker Compose Postgres instead
of the live external database.
