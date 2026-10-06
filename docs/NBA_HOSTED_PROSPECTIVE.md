# Making V1.5 outlooks runnable in the hosted environment

**Goal:** freeze genuine V1.5 pregame outlooks for every 2026-27 game, in the
hosted environment, so that Minutes V2 has a fair prospective baseline. No new
modelling: no Minutes V2, points, rebounds, assists, odds or betting logic.

**Status: built and tested; NOTHING applied to production.** Every production
step below needs explicit approval.

## The problem

| Needed at prediction time | Where it is today |
|---|---|
| 2018-19..2025-26 box scores, player history (features) | local SQLite only |
| Current rosters, injury feed, schedule (evidence) | hosted PostgreSQL only (live cycle) |
| Fitted canonical models (V1 minutes, P(play), P(10+)) | local, git-ignored `backend/model_artifacts/` |
| Model registry rows (features, uncertainty table, reconciliation config) | local `nba_minutes_model_runs` only, with absolute Windows paths |
| Schema for snapshots | migrations not applied to production |

## 1. Production migration chain (verified after PR #23)

Linear chain with a single head:

    90343a82a7c8  (production today; pinned image sha-3deef2f)
      -> 47742ee9eb7a  nba_minutes_model_runs, nba_minutes_predictions
      -> c133d5a7a11e  nba_rotation_predictions
      -> 72256bdebf34  nba_outlook_snapshots + nba_rotation_predictions.snapshot_id
      -> dd83e713f375  nba_outlook_snapshots.evidence_freshness   (this branch)

All four only create new tables, or add nullable columns to those new tables.
No existing AFL or NBA-evidence table is altered, and no existing row
changes, so the `db-migrate` workflow's before/after row-count comparison
will pass.

## 2. Are the canonical models in the Docker image? No, not today.

Proof:
- **The fitted files aren't in git:** `backend/model_artifacts/` is
  git-ignored (`git ls-files backend/model_artifacts` returns 0 files), and the
  image is built by CI from a clean checkout (`context: backend`). `master`
  contains no `.pkl` file at all.
- **The pinned image predates the modelling code:** it was built from
  `3deef2f`, whose tree has no minutes or rotation code at all.
- **The registry rows exist only in the local database,** with
  `artifact_path` values like `C:\Users\...\model_artifacts\...pkl`, which are
  unusable in a container.

**Fix (this branch):** `backend/app/nba/serving_models/` ships the three
canonical fitted files (about 5.4 MB), byte-identical to the registry
artifacts. `manifest.json` carries the registry metadata of the four
canonical serving runs and golden predictions:

| Key | Local run | Version | SHA-256 | Notes |
|---|---:|---|---|---|
| minutes | 36 | minutes-v1.0 | `cef5829d526f…` | code `fad1dc7` |
| participation | 43 | rotation-v1.0 | `f5a0fc5f54f8…` | code `ed4955a` |
| rotation_10 | 44 | rotation-v1.0 | `49afbc289651…` | code `ed4955a` |
| reconciliation | 45 | rotation-v1.0 | (config only) | `adopted: false` |

- `verify-serving-models` checks every file's SHA-256, loads it, and
  reproduces golden predictions on fixed inputs (max diff ≤ 1e-9). **CI now
  runs this inside the built image** (Docker build job), which proves on Linux
  and in the exact image that the models are present, unmodified, and behave
  identically.
- `import-serving-models` writes the four runs into a database's registry.
  It is append-only and idempotent, and artifact paths are relative to the
  backend root, so they resolve inside the image. **Nothing is ever retrained
  in production.**
- Local-only metadata this replaces: registry rows 36, 43, 44 and 45 (V1
  features list, uncertainty table, participation features,
  reconciliation config and σ table).

## 3. Historical data the hosted prediction path reads

`load_games` → `nba_games` (all seasons). `load_logs` →
`nba_player_game_logs` for competitive games from 2018-19.
Also read: `nba_players` (identity, via roster source ids), `nba_teams`,
`nba_team_observations` (rosters), `nba_player_availability_reports`,
`nba_evidence_polls`, `nba_game_schedule_observations` (all hosted already),
and `nba_minutes_model_runs` (imported from the bundle).

Local reference counts the hosted backfill should approximately reproduce
(ESPN may have applied stat corrections since):

| Season | Competitive final games | Stored box scores | Coverage | Player logs | Players |
|---|---:|---:|---:|---:|---:|
| 2018-19 | 1,312 | 1,289 | 0.982 | 32,361 | 545 |
| 2019-20 | 1,143 | 1,141 | 0.998 | 28,663 | 538 |
| 2020-21 | 1,171 | 1,164 | 0.994 | 31,372 | 544 |
| 2021-22 | 1,323 | 1,323 | 1.000 | 33,857 | 623 |
| 2022-23 | 1,320 | 1,320 | 1.000 | 34,033 | 549 |
| 2023-24 | 1,319 | 1,319 | 1.000 | 35,004 | 589 |
| 2024-25 | 1,321 | 1,321 | 1.000 | 35,198 | 580 |
| 2025-26 | 1,322 | 1,310 | 0.991 | 34,478 | 591 |

2015-16..2017-18 are not imported. No technical requirement needs them,
because the models' history window starts at 2018-19.

## 4. Hosted backfill workflow (`.github/workflows/nba-hosted-backfill.yml`)

- **Trigger:** manual dispatch only. One season per run, chosen from 2018..2025
  only, and confirmed by typing `backfill-<season>`.
- **What it runs:** the existing, idempotent, resumable ESPN ingestion,
  `python -m app.nba.cli backfill --from-season S --to-season S
  --keep-player-display`, followed by the read-only
  `season-coverage --season S`. The coverage check fails if fewer than 95% of
  competitive final games have a stored box score. It also prints coverage
  before the run.
- **Never** restores or replaces a database, deletes anything, runs alembic,
  or calls AFL code (a test asserts these strings are absent).
- **Live evidence is untouched:**
  - season date ranges end 30 June, so the 2026-27 schedule isn't touched;
  - `--keep-player-display` stops historical box scores from changing a
    player's current team, name or position (a new ingestion option, tested);
  - schedule observations for historical games are added only where the game
    is new (append-only, honestly stamped with the time we observed them).
- **The live cycle isn't disturbed:**
  - the backfill has its own concurrency group, so it never blocks or queues
    behind the 15-minute cycle;
  - the live cycle's own box-score step only looks back 3 days, so it never
    picks up the historical games.
- **Image:** uses the pinned live-cycle image. An image without hosted-backfill
  support (anything before this branch) is refused before anything runs.
- **Rerunning** continues from where it stopped, because dates and games
  already settled are skipped.

**Estimated impact** (from the local equivalents):
- About 13,100 ESPN requests in total (2,841 dates + 10,231 box scores),
  roughly 1,600 per season, at the existing 0.5-second spacing. That's 20–30
  minutes per season and about 3–4 hours for all eight, run sequentially.
- About 10,800 games, 265,000 player-log rows, about 1,300 players and about
  10,800 schedule observations.
- Estimated about 100 MB in PostgreSQL including indexes. **This is an
  estimate, not a measurement.** The hosted provider's free-tier storage
  limit isn't recorded in this repository and should be checked in the
  provider dashboard before backfilling.

**Recommended order:** 2025, 2024, 2023, …, 2018. The most recent seasons
matter most for 2026-27 features.

## 5. Evidence freshness (frozen with every snapshot)

| Source | Role | Fresh | Stale | Unavailable |
|---|---|---|---|---|
| Roster observation (polled daily) | required: defines the candidates | ≤ 36 h | ≤ 7 days | older or none: that team gets no rows |
| Box scores of either team's final games in the last 7 days | required for complete features | all stored | any missing | — |
| Schedule observation of the game (polled every 3 h) | timing | ≤ 6 h | ≤ 48 h | older or none (counts as stale) |
| Injury feed (polled every 30 min) | stored beside predictions only; never used by V1.5 | ≤ 90 min | ≤ 24 h | older or none |

Snapshot status, most severe first:

| Status | Meaning |
|---|---|
| `missing_data` | no rows written |
| `partial` | a team lacked a usable roster |
| `stale` | rows written, but some required evidence was stale; **not** a normal success |
| `produced` | all required evidence fresh |
| `missed_window` | the runner arrived too late for this label |

A roster's age is measured from the last poll at or before the cutoff that
still showed the same content (`roster_confirmed_at`), not from when it last
changed. Observations are written only on change, so an unchanged roster
would otherwise look older every day, and after 7 days its team would get no
rows. The first hosted dry run (2026-10-06, run 37406099955) showed every
roster as about 68 hours old for this reason.

Actual ages and classes are stored in `evidence_freshness`. A 17-day-old
roster is unavailable: no rows, `missing_data`. The snapshot never falls back
to a reconstructed historical roster, and never uses evidence observed after
the cutoff.

## 6. Automatic snapshot execution

- A new step in `nba-live-cycle.yml` runs after the evidence step:
  `python -m app.nba.outlooks.cli snapshot`. It uses the same pinned image and
  database and the same concurrency group. It runs even if the evidence step
  reported staleness, because the snapshot records that.
- It is gated by a new repository variable, `NBA_OUTLOOK_SNAPSHOTS_ENABLED`.
  **Unset means off.**
- Every 15-minute wake checks each upcoming game against T-24h, T-4h, T-1h
  and T-30m. Only due windows are written.
  - Tolerance is min(30 min, offset / 2): 30 minutes for T-24h, T-4h and
    T-1h, which gives two chances at 15-minute cadence; 15 minutes for T-30m.
  - A late arrival is recorded as `missed_window` and never manufactured.
  - Frozen snapshots are never redone.
- Cost per wake:
  - nothing due: one schedule query;
  - something due: loads history (about 265k rows) once per run, builds
    features in about 5 s, then makes predictions.

## 7. Cron changes (this branch, normal PR/CI)

| Trigger | Old | New |
|---|---|---|
| Cloudflare primary (`wrangler.toml`) | `4,19,34,49 * * * *` | `9,24,39,54 * * * *` |
| GitHub fallback (`nba-live-cycle.yml`) | `11,26,41,56 * * * *` | `14,29,44,59 * * * *` |

- The new minutes don't conflict with the AFL live cycle (7, 22, 37, 52) or
  the weekly backup (Sunday 03:17).
- This is a precaution after two "pooler is shutting down" failures at 05:04Z
  and 06:04Z. It is not a proven cause.
- The GitHub change takes effect when merged. **The Cloudflare change takes
  effect only when the Worker is redeployed** (`npx wrangler deploy`), which
  is a separate step needing your approval.

## 8. Safe production cutover (needs explicit approval)

The pinned image and the database must move together. `db-migrate` always
uses the pinned image, and the live cycle refuses to run (exit 3) whenever
the database is not at its image's head.

1. **Merge** this branch with green CI. The master push builds and pushes
   `ghcr.io/srat0029-ui/afl/backend:sha-<merge>`, and CI verifies the serving
   bundle inside it.
2. **Backup:** dispatch `backup.yml` and confirm it succeeds (encrypted dump,
   verified).
3. **Verify the current revision:** dispatch NBA Live Cycle →
   `schema-status`, with the old image still pinned. Expect `90343a82a7c8`.
4. **Pause automated cycles:** set `NBA_LIVE_CYCLE_ENABLED=false`. This gates
   both GitHub's schedule and the Cloudflare dispatches. The gap starts here.
5. **Re-pin:** set `NBA_LIVE_CYCLE_IMAGE` to the new `sha-<merge>` image.
6. **Migrate:** dispatch `db-migrate.yml` with
   `expected_current_revision=90343a82a7c8` and `confirm=migrate`. It records
   row counts before and after, and fails if any existing table changed.
7. **Verify the schema:** NBA Live Cycle → `schema-status`. Expect
   `dd83e713f375`, current.
8. **Manual cycle:** NBA Live Cycle → `run-live-cycle`. Expect exit 0.
9. **Evidence health:** NBA Live Cycle → `evidence-health`. Expect OK.
10. **Resume:** set `NBA_LIVE_CYCLE_ENABLED=true`. The gap ends. Confirm the
    next automatic run succeeds.

Expected gap: 15–25 minutes, which is one or two skipped ticks.

**Rollback:** alembic runs the whole upgrade in a single transaction
(`alembic/env.py`: one `begin_transaction()` around all migrations), so on
PostgreSQL the four migrations apply together or not at all. If step 6
fails, the database stays at `90343a82a7c8`. Re-pin `sha-3deef2f` and resume. If it
fails after migrating, keep the new image: it matches the migrated schema.

**After the cutover**, each step needs separate approval:
- **(a)** NBA Outlook Admin → `verify-serving-models`, then
  `import-serving-models`.
- **(b)** Hosted backfill, seasons 2025 → 2018.
- **(c)** NBA Outlook Admin → `snapshot-dry-run`, and review the output.
- **(d)** Set `NBA_OUTLOOK_SNAPSHOTS_ENABLED=true`.
- **(e)** Redeploy the Cloudflare Worker with the new cron.

The 2026-27 regular season starts 20 October 2026.

## 9. Season-opener finding (unchanged, not patched)

- Reconstructed historical universes contain departed players early in a
  season. The observed current roster removes them prospectively, and it is
  what defines the hosted candidates.
- Even among genuine roster players, historical early-season P(play) was 0.646
  predicted against 0.712 observed.
- No correction is applied. The canonical learned probabilities stay exactly
  as evaluated, so V2 gets a fair baseline.

## 10. Risks to live evidence collection

- **Cutover window** (steps 4–10): one or two ticks paused on purpose.
- **A cycle between re-pin and migrate** would exit 3 and change nothing;
  pausing first prevents it.
- **Backfill load on the hosted database:** steady inserts for hours,
  concurrent with live cycles, with no shared lock. The writes touch different
  games, though both touch `nba_players` rows (live feeds may create players
  the backfill then updates logs for). The ingestion's idempotent upserts make
  this safe, but a pooler hiccup could fail a backfill run; just rerun it.
- **Storage limit:** about 100 MB extra (estimate). Unknown provider limit;
  check it first.
- **Snapshot step** (once enabled): adds a few seconds per wake, and about a
  minute when snapshots are due. A failure turns the run red after evidence
  was already collected.
- **The :04 pooler failures:** the cron move is a precaution only. The cause
  is unproven.
