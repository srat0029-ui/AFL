# NBA Stat Model V1: results

Rules: [NBA_STAT_MODEL_V1_PLAN.md](NBA_STAT_MODEL_V1_PLAN.md), pushed (c5f4ca1)
before the code existed. Reports: `docs/nba_stat_model_v1/selection-20261030T093850Z.json`
and `test-20261030T094130Z.json` (code 98e343c, committed before the test stage
ran; it did not change afterwards). Local database only; nothing served.

## Decision

| Stat | Mean rule | Distribution rule |
|---|---|---|
| Points | **Pass** | **Fail**: 80% interval coverage 76.8%, near-mean line off by 2.8 points |
| Rebounds | **Pass** | **Pass** |
| Assists | **Pass** | **Pass** |

**Points probabilities are not good enough to price lines near the mean yet.**
Points means, and all rebounds and assists outputs, passed. None of this is
served, and the real test is frozen pregame predictions in 2026-27.

## Means

Test seasons 2024-25 and 2025-26, 56,494 played rows, all conditional on playing:

| Model | Points MAE | Rebounds MAE | Assists MAE |
|---|---:|---:|---:|
| Season to date | 4.700 | 1.937 | 1.357 |
| Last 10 | 4.692 | 1.948 | 1.365 |
| EWMA half-life 4 | 4.626 | 1.923 | 1.345 |
| EWMA half-life 10 | 4.638 | 1.917 | 1.344 |
| Rate × recent minutes | 4.600 | 1.894 | 1.333 |
| **`stat_v1`** | **4.523** | **1.866** | **1.319** |
| Hindsight: rate × actual minutes (not usable) | 3.710 | 1.585 | 1.198 |

Improvement over the closest baseline, rate × recent minutes (95% CI, clustered
by date): points 0.077 [0.064, 0.091], rebounds 0.028 [0.023, 0.034], assists
0.015 [0.011, 0.018]. Every baseline comparison excludes 0 and the selection
folds agree. The gains are real but small: 1.1–1.7% against the closest
baseline, 2.8–3.8% against a season average.

Bias is small: points −0.04, rebounds +0.04, assists −0.02.

Hindsight minutes would cut MAE by a further 18% (points), 15% (rebounds) and
9% (assists). Minutes remain the largest error we can see, though most of the
error is game-to-game variation in the stat itself.

## Where the gain is not

The improvement is concentrated in lower-minute players. **For players with
V1 expected minutes of 32+, who are the core of prop markets, `stat_v1` does
not beat a plain EWMA:**

| V1 expected minutes | Rows | Points: V1 / EWMA 10 | Rebounds | Assists |
|---|---:|---:|---:|---:|
| < 15 | 12,816 | 3.04 / 3.29 | 1.47 / 1.59 | 0.84 / 0.90 |
| 15–25 | 18,767 | 4.29 / 4.43 | 1.85 / 1.91 | 1.22 / 1.25 |
| 25–32 | 15,201 | 5.10 / 5.16 | 2.04 / 2.05 | 1.49 / 1.50 |
| 32+ | 9,710 | **6.04 / 6.02** | **2.15 / 2.15** | **1.88 / 1.87** |

These bands were not bootstrapped and were not part of the decision rule. Any
market edge would have to be found where books set lines most carefully, and
this model has no accuracy advantage there. That is the main reason not to
expect mispricing from V1 alone.

Other segments, points MAE (V1 / EWMA 10): first game with a new team 3.70 / 4.24
(446 rows), new team under 10 games 4.07 / 4.41, first 5 games of a season
4.13 / 4.43, career under 10 games 3.24 / 3.33, playoffs 4.43 / 4.73. Assists are
the one exception among thin histories: 0.97 / 0.96 for career under 10 games.

## Distributions

Test seasons, `stat_v1` means:

| | Points | Rebounds | Assists |
|---|---:|---:|---:|
| 80% coverage: Poisson | 49.6% | 71.6% | 75.5% |
| 80% coverage: one NB α | 69.0% | 77.7% | 78.8% |
| 80% coverage: **NB by bucket** | **76.8%** | **79.3%** | **79.9%** |
| Below-mean line: predicted / observed | 0.501 / 0.521 | 0.625 / 0.618 | 0.663 / 0.670 |
| Above-mean line: predicted / observed | 0.421 / 0.449 | 0.440 / 0.437 | 0.427 / 0.430 |

NB by bucket beats Poisson and one α on log score for every stat, with CIs
excluding 0, and its CRPS beats every baseline. Points fail twice: intervals are
too narrow and probabilities near the mean are too low. Selection folds already
showed the same direction (76.7%; gaps 0.013 and 0.019). Fixed-threshold points
probabilities are closer: P(≥20) 0.155 vs 0.159, P(≥30) 0.038 vs 0.037, but
P(≥10) 0.459 vs 0.474.

A likely cause, not yet tested: the negative binomial is a poor shape for points,
which come in 1s, 2s and 3s and vary with minutes, so a mixture over minutes is
the natural next candidate. It would be a new version, judged on data not used
here.

## Limits

- **Grid edge.** Shrinkage chose k = 20 minutes, the grid's lowest, in every
  fold and stat, and half-life chose 10, the lowest, for points and assists. Less
  shrinkage or shorter half-lives may do better. The grid was not widened after
  seeing this, because it is pre-registered.
- **No availability.** Results are conditional on playing, and V1 minutes do not
  know who else is absent. P(play) is unchanged and is not part of this.
- **Test-fold use.** These folds have now been read once for stats. They can't
  serve as an unseen test for later versions; 2026-27 prospective data is the
  fair judge.
- **Multiple comparisons.** No correction across stats and baselines; the CIs
  are far from 0, so it would not change a mean decision.
- **Fold 2020-21 tuning.** V1 has no out-of-sample minutes for 2019-20, so that
  fold's validation used recent minutes. This detail was not in the plan.
- **Zero-minute rows.** 831 played rows with 0 recorded minutes stay in the
  scoring, per the plan.

## Reproduce

    python -m app.nba.stats.experiment selection --minutes-cache <file>
    python -m app.nba.stats.experiment test --minutes-cache <file>
