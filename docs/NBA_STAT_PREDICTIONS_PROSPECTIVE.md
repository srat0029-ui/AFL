# Frozen Stat Model V1 predictions (2026-27)

**Purpose:** collect genuine pregame points, rebounds and assists predictions
now, so that Stat Model V1 and its baselines are judged on games that had not
happened when they were written.

No odds, recommendations, staking or user interface. This is off until the
repository variables below are set.

## What runs

After the V1.5 snapshot step, each live cycle can run:

    python -m app.nba.stats.cli freeze

For each V1.5 snapshot labelled T-4h or T-1h that has player rows and no stat
predictions yet, it writes three rows per player to `nba_prop_projections`.

| Field | Source |
|---|---|
| `predicted_mean` | Shrunk rate × the snapshot row's frozen minutes |
| `expected_minutes`, `inputs.p_play` | Copied from the V1.5 row, never recomputed |
| `rate_per_minute` | From box scores known at the cutoff |
| `information_cutoff` | The snapshot's own cutoff |
| `generated_at` | The moment of writing |
| `model_version` | `stat-v1.0` |
| `distribution_kind` | `nb_bucket`, or `nb_bucket_uncalibrated` for points |
| `distribution_params` | Negative binomial α |
| `inputs.snapshot_id`, `inputs.rotation_prediction_id` | The exact V1.5 inputs |
| `inputs.baselines` | All five baseline means, frozen on the same row |
| `inputs.parameters_sha256` | The hash of `serving_v1.json` |
| `inputs.validation` | The recorded offline status |

**No migration is needed.** The table has been in production since the
foundation work, is empty there as far as we know, and is append-only through
the ORM.

`serving_v1.json` is copied from the recorded 2025-26 fold, and a test checks
it against the report. Nothing is fitted in production.

## Validation status

| Stat | Mean | Distribution | `calibrated` |
|---|---|---|---:|
| Points | Passed offline | **Failed offline** | `false` |
| Rebounds | Passed offline | Passed offline | `true` |
| Assists | Passed offline | Passed offline | `true` |

"Calibrated" here means only that it passed the offline rule. It is not
evidence of prospective calibration or of market value. Any future display must
read this status from the row. A changed status is a new model version.

## Guarantees

- **Before tip-off.** A write needs the game still scheduled and the time before
  tip-off, enforced in `app/nba/prospective.py`.
- **Inside the window.** A write is refused after the snapshot's target cutoff
  plus its tolerance (30 minutes for both labels). A failed step can retry on
  the next cycle within that time, and after it the gap stays a gap.
- **No later information.** History uses the 4-hour result lag, and excludes any
  box score this system recorded after the cutoff.
- **Immutable.** The ORM guard blocks edits and deletes. Bulk SQL would bypass
  it, as with the other frozen tables, and no code here uses bulk SQL.
- **Idempotent.** One transaction per snapshot, plus the table's unique identity.

## Failure isolation

- Its own step, after the snapshot step, with `continue-on-error: true` and a
  5-minute timeout. A failure leaves a warning and the run stays green.
- Its own pinned image, `NBA_STAT_PREDICTIONS_IMAGE`. Evidence and V1.5 snapshots
  stay on `NBA_LIVE_CYCLE_IMAGE`, so activating this does not repin V1.5.
- It writes only to `nba_prop_projections`, and a test confirms V1.5 rows do not
  change.
- The watchdog is unchanged and does not monitor this step. Gaps show in the
  evaluation report. Alerting can wait until collection has proved useful.

## Activation, after approval

1. Merge, and note the image digest CI builds.
2. Set `NBA_STAT_PREDICTIONS_IMAGE` to that pinned digest.
3. Run `nba-stat-predictions.yml` with `freeze-dry-run` when a snapshot is due.
   It is read-only, and reports rows, time and memory.
4. Set `NBA_STAT_PREDICTIONS_ENABLED=true`.
5. Check the next due snapshot, then `evaluate`, and confirm V1.5 counts still
   grow as before.

To stop, set `NBA_STAT_PREDICTIONS_ENABLED=false`. Frozen rows stay.

## Capacity (measured 2026-11-02)

| | |
|---|---|
| Database size | 159.6 MB (Layerbase shows 266.4 MB on disk) |
| Plan storage | 10 GB on Solo; Layerbase's page doesn't say per database or in total |
| Snapshots so far | 112 (108 produced, 4 missed window), 3,703 outlook rows |
| Rows per snapshot | About 34 |
| Existing outlook row | About 1.9 KB including indexes (7.02 MB / 3,703) |
| Snapshot step, last 40 cycles | 12.6 s mean, 118 s maximum |

Estimate for the rest of the season: about 1,200 games × 2 labels × at most 34
players × 3 stats ≈ 245,000 rows. At the outlook row's 1.9 KB, that is at most
about 465 MB. These rows should be smaller, and players without minutes get
none. The dry run replaces this estimate with measured figures, though it
doesn't measure write time.

## Evaluation

    python -m app.nba.stats.cli evaluate

Read-only, and scores only frozen rows. It recomputes no prediction or baseline.
For each stat and label it reports:

- frozen, pending, void and scored rows, and any integrity violations
- MAE, RMSE and bias for V1 and all five baselines
- paired bootstrap against each baseline, clustered by date
- log score, CRPS, 80% coverage and near-mean line calibration, beside the status

A DNP or a missing box score is void, never a zero.

**Rule for reading it:** V1 is frozen, so every game frozen after activation is
an independent test. Report running numbers freely, but draw no conclusion before
300 final games have rows at T-1h, the bar Minutes V2 uses. T-1h is the primary
label. Nothing about `stat-v1.0` changes as a result of looking.

## Improving the model later

Minutes-uncertainty mixtures for points, and teammate absences in scoring rates,
each become a **new `model_version`** written beside V1 on the same snapshots.

- 2018-19 to 2025-26 may be used for development, but never as confirmation.
  The 2024-25 and 2025-26 stat outcomes have already been read.
- The plan and decision rule are committed before that version starts freezing.
- It is judged only on 2026-27 games it froze before tip-off, paired with V1 at
  the same cutoffs. Rows made before its activation can't count.
- V1 keeps freezing, and its results stay as recorded.
