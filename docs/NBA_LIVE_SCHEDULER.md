# NBA live scheduler (hosted)

How the NBA live evidence cycle (see [NBA_LIVE_EVIDENCE.md](NBA_LIVE_EVIDENCE.md))
runs without any personal computer being on: a GitHub Actions workflow
against the persistent hosted PostgreSQL database.

**Status: built, not active.** The workflow is committed but cannot run on
a schedule yet, and the hosted database has not been migrated. Nothing in
this document has been applied to production. The activation steps are at
the end and each needs an explicit go-ahead.

Branch sequence: `feature/nba-data-sourcing` → `feature/nba-live-evidence`
→ master → `feature/nba-live-scheduler`. This branch is stacked on the
first two; none has been merged.

## The workflow

`.github/workflows/nba-live-cycle.yml`

| | |
| --- | --- |
| Trigger | **Primary:** a Cloudflare Worker cron at `9,24,39,54 * * * *` that calls `workflow_dispatch` (see [Cloudflare trigger](#cloudflare-trigger)). **Backup:** GitHub's own `schedule` at `14,29,44,59 * * * *`, staggered 5 minutes after it. (Moved from 4,19,34,49 / 11,26,41,56 after two hosted-pooler failures at :04 on 2026-10-04 - a precaution; the Worker's new cron takes effect only when it is redeployed.) Plus manual `workflow_dispatch`. |
| What it runs | `python -m app.nba.cli run-live-cycle` inside a pinned CI-built image, with `DATABASE_URL` from the existing repository secret |
| Manual options | `run-live-cycle` (with optional `force`), `schema-status` (read-only), `evidence-health` (read-only) |
| Concurrency | `group: nba-live-cycle`, `cancel-in-progress: false` |
| Timeout | 20 minutes |
| Never does | run migrations, use SQLite, spend Odds API quota |

### Gates

Nothing touches the database until all of these are in place:

1. **Default branch.** GitHub only runs scheduled workflows from the
   default branch, so the schedule cannot fire from a feature branch.
2. **`NBA_LIVE_CYCLE_ENABLED`** repository variable. A scheduled run, or a
   Cloudflare cron dispatch (`trigger=cloudflare-cron`), does nothing unless
   it is `true`. A manual dispatch always runs.
3. **`NBA_LIVE_CYCLE_IMAGE`** repository variable: the image to run, pinned
   to a `sha-<commit>` tag or an `@sha256:` digest. The job refuses an
   unpinned tag, so production never silently picks up new code.
4. **`DATABASE_URL`** repository secret (already exists; used by the AFL
   workflows). The job refuses to start without it.
5. **Schema check.** The command reads the database's migration revision
   and refuses to run (exit 3) unless it equals the code's head.

### Exit codes

The container's exit code is the job's result.

| Code | Meaning | Job |
| --- | --- | --- |
| 0 | The cycle ran. A source that failed this time is printed as a warning annotation. Also returned when another cycle held the lock. | green |
| 1 | The cycle ran, but some evidence has not been collected for more than 3× its interval: a sustained failure. | red |
| 2 | A step failed for an internal reason (a bug, a database error). | red |
| 3 | The database is not at the migration head. Nothing was run. | red |

A single failed read of ESPN is expected occasionally and does not turn the
job red; a red job that is routinely ignored would hide real failures.
Sustained failure turns it red through the staleness check.

## One run at a time

- **GitHub:** the concurrency group serialises runs. An overrunning run
  finishes; at most one run waits behind it, and older waiting runs are
  dropped.
- **Database:** the cycle takes a PostgreSQL session-level advisory lock on
  a dedicated connection for the whole run. A second process that cannot
  take it exits without doing anything. If the process dies, PostgreSQL
  drops the connection and the lock, so a crash cannot leave a stale lock.
  Holding the lock also proves any run still marked "in progress" is dead,
  so it is closed out immediately rather than after a timeout.
- **Writes:** every evidence write is idempotent and in its own
  transaction, so even an overlap that got past both locks could not
  duplicate a row.

## Cloudflare trigger

GitHub's `schedule:` is best-effort: runs are often late and sometimes
skipped entirely. The primary trigger is therefore a Cloudflare Worker
cron, `cloudflare/nba-live-cycle-trigger/`, whose only job is to call
GitHub's workflow-dispatch API:

```
Cloudflare cron (9,24,39,54 * * * *, UTC)
  -> POST /repos/srat0029-ui/AFL/actions/workflows/nba-live-cycle.yml/dispatches
     ref=master, inputs: command=run-live-cycle, force=false,
                         trigger=cloudflare-cron, dispatch_id=cf-YYYYMMDDTHHMMZ
  -> this workflow -> pinned image -> hosted PostgreSQL
```

- Nothing NBA runs in Cloudflare, and the Worker never touches the database.
- One cron tick makes exactly one request. No retries: a rejected or failed
  dispatch is logged and thrown, and GitHub's `schedule:` stays as the
  backup. If both fire, the concurrency group, the advisory lock and the
  idempotent writes make the extra run harmless.
- The Worker has no public URL (`workers_dev = false`), so nobody can
  trigger a dispatch through HTTP.
- The `dispatch_id` appears in the GitHub run name (`NBA Live Cycle
  [cf-20261003T0649Z]`) and in the run's first step, so every Cloudflare
  tick can be matched to its GitHub run.

**Token.** A fine-grained personal access token, repository access limited
to `srat0029-ui/AFL`, with the single repository permission **Actions:
Read and write** (GitHub adds Metadata: Read automatically). Stored only as
the Cloudflare Worker secret **`GITHUB_DISPATCH_TOKEN`**; never in the
repository.

**What each signal means** (do not read more into one than it says):

| Signal | Where | Proves |
| --- | --- | --- |
| `{"event":"trigger_fired",...}` | Cloudflare Worker logs | The cron fired. |
| `{"event":"dispatch_accepted",...}` | Cloudflare Worker logs | GitHub accepted the dispatch request (HTTP 200/204). **Not** that a run started or succeeded. Includes `workflow_run_id` when GitHub returns it. |
| `{"event":"dispatch_rejected","status":...}` / `dispatch_error` | Cloudflare Worker logs | The dispatch did not happen (bad/expired token → 401/403, workflow input mismatch → 422, network/timeout). |
| A run named `NBA Live Cycle [cf-…]` | GitHub Actions | The workflow ran for that tick. Its "Log trigger" step prints the trigger and dispatch id. |
| That run's conclusion | GitHub Actions | Whether the cycle itself succeeded (see exit codes above). A skipped job means `NBA_LIVE_CYCLE_ENABLED` is not `true`. |

**Deploying** (needs a Cloudflare account; free plan is enough):

1. Merge the workflow change first. Until `master` has the `trigger` and
   `dispatch_id` inputs, GitHub rejects the Worker's dispatch with 422.
2. `cd cloudflare/nba-live-cycle-trigger`, `npx wrangler login`,
   `npx wrangler deploy`.
3. `npx wrangler secret put GITHUB_DISPATCH_TOKEN` (or the dashboard:
   Workers & Pages → nba-live-cycle-trigger → Settings → Variables and
   Secrets → Add → type *Secret*). Until it is set, each tick logs
   `dispatch_error: missing GITHUB_DISPATCH_TOKEN` and makes no request.
4. Test once without waiting for the cron:
   `npx wrangler dev --remote --test-scheduled`, then in another terminal
   `curl "http://localhost:8787/__scheduled?cron=9,24,39,54+*+*+*+*"`.
   This makes one real dispatch. If the remote session does not see the
   deployed secret, run plain `npx wrangler dev --test-scheduled` with the
   token in a local `.dev.vars` file (gitignored) instead.

Tests: `cd cloudflare/nba-live-cycle-trigger && npm test` (Node 22, no
dependencies; also run in CI).

## Adaptive polling

The workflow wakes every 15 minutes; the application decides what is due.
All values are settings (see the table in NBA_LIVE_EVIDENCE.md):

| Source | Default |
| --- | --- |
| Injury / availability feed | every 30 minutes |
| Schedule (3 days back, 14 ahead) | every 3 hours |
| Team rosters | daily |
| Depth charts | daily |
| Box scores for finished games | hourly |
| Pregame lineups | none beyond 24h before tip-off; hourly 24h–4h; every 30 min 4h–1h; every 15 min in the final hour; none after tip-off |

The lineup tiers exist to discover when ESPN begins publishing starters.
The cron interval (15 minutes) is the finest resolution any tier can
actually achieve.

A busy game day is roughly 1,350 requests to ESPN, mostly lineups.

## Failure behaviour

- A source failure in one step records nothing for that step — no poll, no
  observation — and leaves every earlier observation untouched. The step is
  due again on the next wake-up.
- One failing step does not stop the others.
- There are no retries inside a run; the next scheduled wake-up is the
  retry, so the request rate to ESPN stays predictable.
- A broken-looking injury feed (fewer than half the previously listed
  players) is rejected rather than recorded.

## Monitoring

`python -m app.nba.cli evidence-health`, the `evidence-health` dispatch
option, and a new `live_evidence` section in `GET /api/nba/status` all
report:

- the latest run and the latest fully successful run;
- the latest successful poll of each source, its age, and whether it is
  stale (older than 3× its interval);
- the number of upcoming games, games inside the lineup window, and current
  availability entries;
- a list of problems, empty when healthy.

A source is only judged when it should be running: lineups only when a game
is inside the 24-hour window, box scores only when a game has recently
finished.

Limits of this monitoring:

- If the schedule stops firing altogether — the variable turned off, the
  workflow disabled, or GitHub's 60-day inactivity rule — nothing runs, so
  nothing fails. Someone has to look: the Actions tab, or a manual
  `evidence-health` dispatch, which will report the stale sources.
- The backend API is not deployed, so `/api/nba/status` is not reachable
  for the hosted database today; the dispatchable `evidence-health` is the
  way to check it.
- GitHub notifies about failed scheduled runs only the user who last
  changed the schedule.

## The hosted database today

Found by reading the repository's workflows and deployment docs and the
public GitHub Actions run history — **not** by connecting to the database
(no credentials were used):

- **Provider:** an external free-tier managed PostgreSQL 18 (Layerbase),
  reached through the `DATABASE_URL` repository secret (a direct,
  non-pooled connection string).
- **Users:** the AFL `live-cycle.yml` and `backup.yml` workflows. The AFL
  backend and frontend on Render have never been deployed.
- **AFL schedule is off.** All 195 scheduled AFL live-cycle runs since
  2 September were skipped (`LIVE_CYCLE_ENABLED` is not `true`). The last
  run that touched the database was a manual run on 10 September 2026 at
  08:32 UTC — the run the AFL prospective boundary is pinned to.
- **No backup has ever been taken.** All `backup.yml` runs were skipped
  (`BACKUP_ENABLED` is not `true`), and no manual backup run exists.
- **It may be archived.** The deployment docs record that the provider
  archives a database left idle for 14+ days and that it then needs a
  manual restore. Its last known activity was 10 September.
- **Probable schema revision: `75715f635e7a`.** That is the migration head
  of the image the AFL cycle runs (`sha-c069109`), and the 10 September run
  succeeded with it. This is inferred, not verified; the `schema-status`
  dispatch verifies it read-only.

### Is it safe to share with NBA?

Yes, with conditions:

- The NBA code reads and writes only `nba_*` tables. Its only reference to
  a shared table is a foreign key from future NBA odds quotes to
  `bookmakers`, and nothing writes NBA odds yet.
- No AFL table is altered by any pending migration; the deployed AFL image
  ignores tables it does not know about.
- The NBA cycle's load is small: a few seconds of writes per run.
- Expected growth is modest (the availability, roster, lineup and schedule
  observations are a few MB per month), but **the free tier's storage
  limit is not documented in this repository and was not verified**.
- The NBA **historical** backfill (2015-16 onward, ~77 MB in SQLite) lives
  only in the local database. The hosted database would start with teams,
  schedule and evidence only. Bringing the history across is a separate
  decision.

Conditions: take a backup first (none exists), confirm the database is not
archived, and confirm its revision with `schema-status`.

If you would rather keep NBA in a separate database, the only change is the
workflow's secret: point `DATABASE_URL` at a different repository secret
(for example `NBA_DATABASE_URL`). The application side is unchanged.

## Migrations that would be applied

From `75715f635e7a` (probable current revision) to `90343a82a7c8`, in order.
The full PostgreSQL DDL, generated offline without connecting to anything,
is in
[sql/production_upgrade_75715f635e7a_to_90343a82a7c8.sql](sql/production_upgrade_75715f635e7a_to_90343a82a7c8.sql).

| Revision | What it does |
| --- | --- |
| `04b59a8a329c` (AFL, already on master) | Creates `player_tag_annotations` (+3 indexes). The migration chain is linear, so this AFL migration has to be applied before the NBA ones. |
| `1e453acf52cb` | Creates 8 tables: `nba_teams`, `nba_games`, `nba_players`, `nba_player_availability_reports`, `nba_player_game_logs`, `nba_prop_projections`, `nba_prop_quotes`, `nba_prop_predictions` (+indexes). |
| `becc7fa40ce6` | Creates `nba_schedule_sync_dates`; adds provenance columns to `nba_games` and `nba_players`; reshapes `nba_player_availability_reports`. |
| `90343a82a7c8` | Creates `nba_evidence_polls`, `nba_team_observations`, `nba_game_lineup_observations`, `nba_game_schedule_observations`, `nba_live_cycle_runs`; reshapes `nba_player_availability_reports` again (drops its unused `reason` column). |

In total: **15 new tables** (1 AFL, 14 NBA), their indexes, and `ALTER`s
that touch only NBA tables created earlier in the same upgrade, which are
empty at that point. **No existing AFL table is altered, and no existing
row is changed** except the single `alembic_version` row. The one `DROP
COLUMN` removes a column from a table created two migrations earlier in
the same upgrade.

Verified locally: the full chain applies to empty PostgreSQL 16 and 18,
downgrades and re-applies cleanly, and leaves no model/schema drift.

## Steps before enabling the schedule

Nothing below has been done. Steps 6 and 9 change the production database
and need your explicit approval.

1. **Merge the branches** in order — `feature/nba-data-sourcing`, then
   `feature/nba-live-evidence`, then `feature/nba-live-scheduler` — each
   through a pull request with green CI. Leave `NBA_LIVE_CYCLE_ENABLED`
   unset; the schedule stays inert.
2. **Pin the image.** After the final merge, CI pushes
   `ghcr.io/srat0029-ui/afl/backend:sha-<merge commit>`. Set the repository
   variable `NBA_LIVE_CYCLE_IMAGE` to that exact tag.
3. **Check the database is awake** in the Layerbase dashboard, and restore
   it if it has been archived.
4. **Take a backup.** Confirm the `BACKUP_ENCRYPTION_PASSPHRASE` secret
   exists, run `backup.yml` manually, and confirm it succeeds. There is no
   backup today.
5. **Confirm the revision.** Run *NBA Live Cycle* manually with command
   `schema-status`. Expect database revision `75715f635e7a` and the four
   pending migrations above. If it reports anything else, stop and review.
6. **Migrate (needs approval).** Run the *Database Migrate* workflow
   (`.github/workflows/db-migrate.yml`) by hand with
   `expected_current_revision = 75715f635e7a` and `confirm = migrate`. It
   uses the same `DATABASE_URL` secret, refuses unless the database is at
   exactly that revision, records every table's row count before and after,
   and fails if any existing table changed. It shares the live cycle's
   concurrency group, so it can never run alongside a cycle.
7. **Confirm** with another `schema-status` run: "schema is current".
8. **First run by hand.** Run *NBA Live Cycle* manually with command
   `run-live-cycle`. Expect exit 0, every step ok, and "evidence health: OK".
9. **Enable the schedule (needs approval).** Set the repository variable
   `NBA_LIVE_CYCLE_ENABLED` to `true`.
10. **Check it is running** after an hour or two with an `evidence-health`
    dispatch or the Actions tab.

Ongoing: GitHub disables scheduled workflows in a public repository after
60 days without commits, and the free database archives after 14 days idle.
The NBA cycle keeps the database active; the repository needs a commit at
least every 60 days.
