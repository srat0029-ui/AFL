# NBA prospective V1.5 outlooks (2026-27)

**Purpose:** start accumulating frozen, pregame minutes and rotation outlooks
for every 2026-27 game. Minutes V2 (V1.5 plus real prospective availability
evidence) will later be judged against these, on exactly the same games.

No betting models: no points, rebounds or assists models, odds,
recommendations, staking or multis.

Run: `python -m app.nba.outlooks.cli snapshot [--labels T-24h,T-4h,T-1h,T-30m] [--dry-run]`

## The modelling position these outlooks freeze

- Participation and rotation prediction (V1.5) is useful, and frozen here.
- Conditional Minutes V1 is useful, and frozen here unchanged.
- Team-minutes reconciliation does **not** improve unseen predictions when
  only genuinely pregame-reconstructable roster information is available, so
  it is not part of the canonical output (`reconciled_minutes_if_plays` is
  null).
- Reconciliation may become useful once real pregame active/inactive evidence
  exists. That is a Minutes V2 question, to be answered against these frozen
  outlooks. ESPN's postgame dressed-player list is never treated as
  historical pregame information.

## What one outlook freezes

**One snapshot** = one row in `nba_outlook_snapshots`, per (game, label,
tip-off as scheduled at the cutoff). It records:

| Field | Meaning |
|---|---|
| `snapshot_label`, `offset_minutes` | e.g. `T-4h`, 240 |
| `scheduled_tipoff`, `target_cutoff` | the tip-off as known at the cutoff, and tip-off − offset |
| `information_cutoff` | the instant actually used: nothing observed later is read |
| `box_score_cutoff` | `information_cutoff` − 4h; latest tip-off a feature box score may have |
| `generated_at` | when the snapshot was written (the prediction timestamp) |
| `status`, `reason` | `produced` / `partial` / `missing_data` / `missed_window`, with an explanation |
| `roster_evidence` | per team: the roster observation used (id, observed_at, number of players, age in minutes), or null |
| `availability_feed_last_read_at` | the latest injury-feed read at or before the cutoff |
| `model_versions` | run id, model name, model version, artifact SHA-256 and code version for minutes, participation, rotation and reconciliation |
| `counts` | candidates, unknown players, players with no history |

**One player outlook** = one `nba_rotation_predictions` row with `snapshot_id`.
It stores, separately:

| Field | Source | Changed by availability evidence? |
|---|---|---|
| `p_play` | V1.5 participation model | **never** |
| `p_rotation` (P(10+ min), capped at `p_play`) | V1.5 rotation model | **never** |
| `tier` | derived from the two probabilities | **never** |
| `expected_minutes_if_plays` | Minutes V1 (null when the player has no history) | **never** |
| `quantiles` (p10–p90) | V1 empirical residual bands | **never** |
| `reconciled_minutes_if_plays` | null (reconciliation not adopted) | n/a |
| `roster_listed`, `team_source` | the roster observation the player came from | stored evidence |
| `availability_evidence` | raw injury-feed state at the cutoff | stored evidence |
| `experimental_rules` | labelled rules, e.g. `experimental_out_status_v0` | stored, never applied |
| `inputs` | every feature value the models consumed | audit |
| `information_cutoff`, `generated_at`, model run ids | | |

Both tables are append-only: a frozen snapshot or outlook can't be edited or
deleted through the ORM. Later evidence produces a **later** snapshot; it
never mutates an earlier one.

## Snapshot timings

Defaults are `T-24h`, `T-4h`, `T-1h` and `T-30m`, configurable with
`--labels` (any `T-<n>h` or `T-<n>m`). The runner is meant to be called
often, for example every 15 minutes. On each call, for each upcoming game and
each label:

| Situation | What happens |
|---|---|
| before the target cutoff | nothing |
| within target + 20 minutes (configurable tolerance) | produced now; `information_cutoff` = now |
| later than that | recorded once as `missed_window`, with no player rows: a "T-24h" made 3 hours before tip-off is not a T-24h |
| already recorded | skipped; frozen, never redone |

Snapshots legitimately see different evidence: a newer roster observation,
an injury report filed between T-4h and T-1h, or a box score that became
final. That difference is the point: it lets us measure how prediction
quality changes as better information arrives. If a game is moved, its
snapshots are taken afresh for the new tip-off; the old ones stay as
recorded.

## Candidate universe

**Prospective (these outlooks):** the team's roster as **observed at or
before the cutoff** (`nba_team_observations`, kind `roster`). This is real
pregame information. Players who left are gone, and traded players appear for
their new team (with `traded = 1` and `games_with_team = 0` in their
features). A roster observed after the cutoff is invisible.

If a team has no roster observation by the cutoff, it gets **no rows**. The
snapshot is `partial`, or `missing_data` if neither team has one, with the
reason; no roster is guessed. Roster players unknown to the database are
counted, not predicted. Players with no history get `p_play` and
`p_rotation` but **no minutes**.

**Historical backtest (how V1.5 was evaluated):** a roster reconstructed from
box-score history alone (latest listing known at the cutoff), because
historical roster observations don't exist. The box-score listing itself is
not used: it is effectively the postgame-published active list. The two
universes differ by design:
- the backtest can't know about departures or signings until a box score
  shows them;
- the prospective run can.

## Season-opener behaviour (investigated, not patched)

Diagnosis, using the 2025-26 walk-forward fold. Whether each candidate was
really on the roster comes from hindsight and is used only for this analysis:

| Team's games | Candidates per team-game | Departed players: mean P(play) / observed | Genuine roster players: mean P(play) / observed |
|---|---:|---:|---:|
| First 3 | 20.4 | 0.137 / 0.000 (31% of candidates) | **0.646 / 0.712** |
| 4 onward | 22.2 | 0.004 / 0.000 | 0.599 / 0.597 |

At season starts the reconstructed historical universe still carries last
season's departed players, and the model learned that early-season
candidates are uncertain. The observed roster removes the departed players,
but for genuine roster players P(play) in the first few games is still
understated, by about 7 points (0.646 vs 0.712). After that it is calibrated.

This is left as is in V1.5:
- the canonical model stays exactly as evaluated;
- 2026-27 opener outlooks should be read as slightly pessimistic on
  participation.

A recalibration conditioned on roster confirmation is a candidate V2
component. It can be fitted honestly only on prospective 2026-27 data.

## V1.5 stays evidence-blind

- `compute_outlooks` reads no availability evidence. `availability_evidence`
  and `experimental_rules` are attached afterwards
  (`outlook_record`), and nothing reads them back into the learned fields.
- A test files an "Out" report between the T-4h and T-1h snapshots and asserts
  that `p_play`, `p_rotation`, `expected_minutes_if_plays` and `tier` are
  identical in both snapshots, while the stored evidence differs.
- Absence from ESPN's injury feed is recorded as `never_listed` ("no
  evidence"), or `no_longer_listed` with no status implied. It is never
  treated as healthy.

## Migration that will be required in production

The hosted database is at `90343a82a7c8`. Before outlooks can be written
there, it needs, in order:

1. `47742ee9eb7a`: `nba_minutes_model_runs`, `nba_minutes_predictions` (V1)
2. `c133d5a7a11e`: `nba_rotation_predictions` (V1.5)
3. `72256bdebf34`: `nba_outlook_snapshots`, plus a nullable `snapshot_id`
   on `nba_rotation_predictions` (this branch)

All three only create new tables or add a nullable column. None alters an
existing AFL or evidence table.

## What has to happen before frozen 2026-27 outlooks can start

The code can run today wherever **both** of these exist:

1. **History:** box scores 2018-19 onward, used for the features. These exist
   only in the local database. The hosted database has none, by decision; the
   live collector only adds 2026-27 box scores as games finish.
2. **Fresh evidence:** current roster observations (required), plus injury
   feed reads (stored beside the outlooks). These are collected by the hosted
   live cycle into the hosted database. The local database's evidence is from
   2026-10-02 and is not being refreshed.

Neither database has both. One of these decisions is needed:

- **(a)** Load the 2018-19..2025-26 competitive box scores into the hosted
  database, apply the three migrations, ship the serving models' files, and
  run the snapshot job on a schedule (a new workflow, triggered alongside the
  live cycle). This needs a new pinned image and production migrations, both
  deliberately not done here.
- **(b)** Run the snapshot job locally against the local database, with
  current evidence pulled from the hosted database (read-only). This needs
  hosted-database credentials on the local machine, and the machine to be on
  at every snapshot time.

Until one is chosen, the runner can only produce outlooks from stale local
evidence, which would be an honest record of what the local machine knew, but
not useful as the V2 baseline.
