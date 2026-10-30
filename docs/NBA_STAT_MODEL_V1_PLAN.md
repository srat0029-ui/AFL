# NBA Stat Model V1: plan and pre-registered evaluation

Written 2026-10-30, **before** any model code existed and before any points,
rebounds or assists outcome from the test seasons was examined. Offline
research only: local database, no migrations, no serving, no workflows, no
frontend, no odds. V1.5 snapshots and the Minutes V2 work are untouched.

## What has already been seen

A scoping script (not committed) read points, rebounds and assists for
**2022-23 and 2023-24 only**, both selection folds. It shaped these choices:
the per-minute × minutes form, a negative binomial with spread that varies by
predicted mean, and the half-life grid below. So selection-fold results are
not independent of the design. Stat outcomes for 2024-25, 2025-26 and 2026-27
have not been read. (Minutes outcomes for the test folds have, through V1.)

## Question

Does an interpretable `E[stat | plays] = expected minutes × shrunk per-minute
rate` beat simple baselines for points, rebounds and assists, and does its
distribution give believable intervals and threshold probabilities?

## Population and targets

- Same seasons and rows as Minutes V1: 2018-19..2025-26, final competitive
  games, one row per box-score listing, eligible = at least one earlier played
  game known at the cutoff.
- Scored rows: `played` and `eligible`, where `played` is V1's definition (not
  did-not-play, minutes recorded). Short appearances, including 0 recorded
  minutes, stay in: props generally stand for anyone who takes the court.
- **Everything is conditional on playing.** DNP rows are never scored as zero
  and never enter a player's rate. P(play) stays V1.5's job and is unchanged.
- Rows with no earlier played game get no prediction, as in V1.

## Information boundary

As V1: a box score counts only if its game is final and tipped off at least
4 hours before the row's tip-off, applied by as-of join. Nothing from the
target game (its listing, starters, minutes, or who else dressed) is a feature.
Minutes input is V1's walk-forward HGB prediction, out of sample in every fold.

## Baselines and model

Means, each scored on identical rows:

| Name | Prediction |
|---|---|
| `season_to_date` | Season average of the stat, falling back to last 10 |
| `last_10` | Last-10 average |
| `ewma_4`, `ewma_10` | Recency-weighted average, half-life 4 or 10 games |
| `rate_ewma` | Unshrunk rate (half-life 25) × recent minutes (half-life 4) |
| **`stat_v1`** | Shrunk rate × V1 expected minutes |

`stat_v1`'s rate is

    (decayed stat + k × prior rate) / (decayed minutes + k)

- Prior rate: league stat per minute by recent-minutes band (under 15, 15–25,
  25–32, 32+), from training seasons only. This is how rookies and thin
  histories are handled.
- Half-life h ∈ {10, 25, 50} games and k ∈ {20, 50, 100, 200} minutes, chosen
  per stat and per fold on season S−1 by MAE, with priors from seasons before
  S−1.
- Traded players keep their rate, and are broken out as a segment.
- Hindsight reference, labelled and never a candidate: `stat_v1`'s rate ×
  actual minutes, which splits a miss into its minutes and rate parts.

Distributions, fitted on earlier folds' out-of-sample predictions only:

| Name | Form |
|---|---|
| `poisson` | Poisson(μ) |
| `nb_single` | Negative binomial with one α per stat |
| **`nb_bucket`** | Negative binomial with α by predicted-mean bucket (method of moments) |

Not in this PR: opponent adjustment, minutes-uncertainty mixtures, teammate
absence and gradient boosting. Each is a later, separately judged step.

## Protocol

- Season walk-forward as V1. Selection folds 2020-21..2023-24; test folds
  2024-25 and 2025-26. Distribution scoring starts at 2021-22, since it needs
  an earlier fold's predictions.
- **The two stages are run separately.** The selection stage reads no test-fold
  stat outcomes. The code is committed before the test stage runs. If the
  selection stage shows a defect, it is fixed under a dated amendment here
  before any test run. Once the test stage has run, nothing about V1 changes
  on the strength of it; a change is a new version judged on 2026-27.

## Metrics

MAE, RMSE, bias, mean log score, discrete CRPS, 80% interval coverage from
randomised PIT, and calibration at fixed thresholds (points 10/15/20/25/30,
rebounds 4/6/8/10, assists 2/4/6/8, as P(stat ≥ N)) and at half-point lines
either side of the predicted mean, where books set them. Segments: V1
predicted-minutes band, season type, first 5 games of a season, team change,
career games under 10, back-to-back.

## Decision rules, fixed now, per stat

Comparisons use the paired bootstrap from Minutes V2: 2,000 resamples clustered
by game date, 95% CI.

**Mean adopted** if `stat_v1` beats **every** baseline on test MAE with a CI
excluding 0, and the selection folds agree in sign.

**Distribution adopted** if all of these hold on test:

1. `nb_bucket` beats `poisson` and `nb_single` on log score, CI excluding 0.
2. `stat_v1` + `nb_bucket` has mean CRPS no worse than every baseline with
   `nb_bucket`.
3. 80% interval coverage is between 77% and 83%.
4. At both near-mean lines, mean predicted probability is within 0.02 of the
   observed rate.

Failing either is reported as a negative result, not tuned away. Passing
adopts nothing into production: serving is a separate decision that needs
approval, and the real test is frozen pregame predictions in 2026-27.
