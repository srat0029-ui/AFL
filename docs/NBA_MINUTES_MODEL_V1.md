# NBA Expected Minutes Model V1

**Question:** can we predict how many minutes an NBA player will play materially
better than simple recent-minutes baselines, using only information available
before tip-off?

**Answer:** yes. The improvement is real but moderate: about 6% lower MAE and 7%
lower RMSE than the best simple baseline, consistent in every season tested.
Almost all of the gain comes from unstable situations: trades, returns from
absence, season starts, playoffs and volatile rotations. For players in stable
roles the model is barely better than an exponentially-weighted average.

Canonical run: `docs/nba_minutes_v1/report-20261003T121144Z.json`, code
`fad1dc7`. The two earlier report files in that folder are identical
re-runs. The first was made before the code was committed, and the second
recorded a spurious "dirty" flag caused by an untracked file. All three
produced the same metrics and a byte-identical serving model
(SHA-256 `cef5829d…e0c6`).

Run it: `python -m app.nba.minutes.cli evaluate` (about 10 minutes). List recorded
runs: `python -m app.nba.minutes.cli runs`.

## Target

**Minutes given that the player plays.** A player prop is void if the player
does not play, so this is the quantity the points/rebounds/assists models
need. Whether he plays at all depends on availability evidence, which V1
deliberately does not use. The unconditional version (DNP counted as 0) is
reported below for completeness.

Minutes are whole numbers: ESPN publishes whole minutes, never seconds.

## Data and seasons

| | |
|---|---|
| Seasons modelled | 2018-19 .. 2025-26 (`season_start_year` 2018..2025), regular season + play-in + playoffs |
| Excluded | 2015-16..2017-18. They stay in the database but are not used even as history, because box scores are structurally missing for New Orleans and Chicago games. 2026-27 is the prospective season, so its outcomes are never used. Preseason is excluded throughout. |
| Box-score listings | 264,966 (47,655 did-not-play, 282 played with no minutes recorded) |
| Played rows with minutes | 217,029 |
| Eligible (have a prediction) | 215,746 (99.4%) |
| Walk-forward evaluation rows | 108,504 selection (2020-21..2023-24) + 56,494 test (2024-25, 2025-26) |

## Eligibility rules

A row is a player listed in the box score of a final competitive game. The
primary target needs the player to have played with minutes recorded.

- **E1, the only exclusion:** the player must have at least one earlier
  played game, with minutes, known at the cutoff and inside the modelled
  window. Players without one get no V1 prediction. That means debuts, and in
  2018-19 anyone whose history predates the window.
- Coverage by season: 98.1% (2018-19), 99.5% (2019-20), then 99.5–99.7%
  in every later season. 2018-19 is only ever training data.
- Nothing else is dropped: traded players, players returning from long
  absences, DNP-heavy players and playoff games all stay in, and they are
  broken out below.
- Ineligible rows get a documented constant fallback: 9.0 minutes, the
  median for no-history rows before the test seasons. On the 209
  test-season rows it scores MAE 6.94, reported separately and not mixed into
  the model figures.

## Leakage protection

Each row is "player X before game Y". Its features are read with as-of joins
at

    t_cut = min(tip-off of Y, prediction time) − 4 hours

A box score counts only if its game is final and tipped off at or before
`t_cut`. That is the same rule as `app/nba/asof.py`'s `game_logs_known_at`
(`GAME_RESULT_AVAILABILITY_LAG`), so a backtest row and a live prediction read
history the same way and go through the same function.

- Every history feature is a cumulative statistic up to and including a
  history row. The as-of join then takes the latest history row at or before
  `t_cut`, so the target game and everything after it are unreachable.
- Nothing from game Y's own box score is used: not its minutes, starter flag,
  DNP flag, or who else was listed. The player's team at Y is the team he
  represented at tip-off. `NbaPlayer.current_team_id` (today's value) is never
  used.
- Schedule context (rest, back-to-back, density, season progress) comes from
  `nba_games`, using earlier games' dates only. A game whose box score is
  missing still counts as a game played.
- No injury information and no DNP reasons are used: there are no trustworthy
  historical pregame injury snapshots. 2026-27 prospective evidence is never
  used in the backtest.
- All preprocessing (imputation medians, scaling, which all-empty columns to
  drop) is fitted inside the model on training rows only.
- **Proof on real data:** every run recomputes the features of 40
  stratified real rows from a copy of the database truncated at each row's
  cutoff. The rows are random, traded, playoff, season-opener, returning,
  DNP and no-history players. In the truncated copy, later box scores are
  deleted and later results reset to unplayed. Result: **0 differences**.
  The same check runs on synthetic data in the test suite.

## Features (41)

| Group | Features |
|---|---|
| Player minutes history | last game, mean of last 3/5/10, median and SD of last 10, EWMA (half-life 4 games), max/min of last 5, games of known history (capped at 82), season-to-date mean and count, previous-season mean, days since last played (capped at 30), team games missed since last played (from the schedule, capped at 20) |
| Role | started last game, start rate over last 5/10, games since last start, DNP in last listing, DNP rate over last 10 listings, consecutive games played |
| Team / rotation context | share of team minutes (last 5), rank in team minutes (last 5), games with current team, minutes with current team (last 5), traded (first game for a new team), team rotation size (last 5 games, players with ≥10 min), minutes vacated in the team's last game by recently-active teammates who did not play, last game's margin |
| Schedule | home, rest days, back-to-back, games in previous 7 days, opponent rest and back-to-back, playoffs, play-in, season progress, team and opponent season point differential (shrunk), absolute strength gap |

Two features were changed after a first look at the selection folds,
because they could not be stable over time. `days_since_played` was uncapped
and `career_games` kept growing with calendar time. Trained on 2018-19 alone,
where there's no off-season gap, a linear model extrapolated season-opener
predictions to 0 minutes. Both are now capped. No test season had been looked
at when this was changed.

Feature-group ablation (HGB, fixed hyperparameters, pooled selection folds),
each group added to the ones above it:

| Features | MAE | RMSE |
|---|---:|---:|
| Player minutes history only | 4.935 | 6.402 |
| + role | 4.903 | 6.369 |
| + team / rotation context | 4.879 | 6.349 |
| + schedule | 4.841 | 6.289 |

Every group helps a little. Player minutes history alone already gets most of
the way there (EWMA alone: 5.121).

Permutation importance (HGB, 2023-24 fold, increase in MAE): EWMA 1.89, last
game 0.42, minutes with current team 0.35, season mean 0.29, started last game
0.16, last-3 mean 0.11, previous-season mean 0.08, season progress 0.06,
rank in team minutes 0.06, team strength 0.04, last margin 0.04, days since
played 0.04. Many features score near zero individually because they're
strongly correlated with each other: dropping one loses little while the
others remain.

## Evaluation protocol (fixed before any test season was examined)

- **Walk-forward by season.** For each season S, fit on all seasons before S
  and predict S. Hyperparameters for S are chosen by an inner chronological
  split: train on seasons before S−1, validate on S−1. No random splits.
- **Selection folds:** 2020-21..2023-24. **Test folds:** 2024-25, 2025-26,
  used for nothing but reporting.
- **Selection rule:** lowest pooled selection-fold MAE among mean estimators.
  Anything within 0.02 minutes of the best counts as a tie, and ties go to
  the simpler model.
- **Season holdout** in addition: a single model trained through 2023-24
  predicts both 2024-25 and 2025-26, so in 2025-26 it is a year stale.
- All models and baselines are scored on exactly the same rows.

## Results

### Test seasons 2024-25 + 2025-26 (walk-forward, 56,494 rows)

| Model | MAE | RMSE | Bias | Median AE | ±2 min | ±4 min | ±6 min | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Last game | 5.865 | 7.877 | −0.014 | 4.00 | 30.9% | 50.3% | 65.0% | 99.6% |
| Last 3 mean | 5.209 | 6.915 | −0.028 | 4.00 | 28.9% | 51.2% | 68.0% | 99.6% |
| Last 5 mean | 5.154 | 6.822 | −0.036 | 4.00 | 28.1% | 50.8% | 68.0% | 99.6% |
| Last 10 mean | 5.244 | 6.906 | −0.051 | 4.10 | 26.7% | 49.7% | 66.7% | 99.6% |
| Season-to-date mean | 5.396 | 7.093 | −0.525 | 4.22 | 26.0% | 48.2% | 65.3% | 99.6% |
| EWMA (half-life 4 games) | 5.059 | 6.658 | −0.046 | 3.97 | 26.8% | 50.3% | 68.0% | 99.6% |
| Ridge | 4.883 | 6.393 | +0.035 | 3.89 | 27.4% | 51.2% | 69.3% | 99.6% |
| Random forest | 4.795 | 6.259 | +0.083 | 3.83 | 27.5% | 51.9% | 70.0% | 99.6% |
| LightGBM | 4.740 | 6.197 | +0.098 | 3.77 | 28.1% | 52.4% | 70.7% | 99.6% |
| **HGB (Model V1)** | **4.742** | **6.205** | +0.103 | 3.77 | 28.3% | 52.5% | 70.5% | 99.6% |
| HGB, absolute loss (median, reference only) | 4.715 | 6.268 | +0.026 | 3.64 | 29.6% | 53.9% | 71.1% | 99.6% |

Bias = mean(predicted − actual). Coverage = share of played rows with a
prediction (rule E1). The "last game" baseline has the highest ±2 hit rate
because whole-minute outcomes often repeat exactly, but it is by far the worst
on MAE and RMSE.

**Is the gain real?** Per-row paired difference in absolute error, HGB vs
EWMA, test seasons, bootstrap clustered by game date (2,000 resamples):
**0.317 minutes, 95% CI [0.283, 0.352]**. HGB vs ridge: 0.141
[0.118, 0.168]. HGB vs LightGBM: −0.002 [−0.007, 0.003], a tie.

### Every season, walk-forward MAE

| Season | Role | Rows | Last 5 | EWMA | Ridge | RF | LightGBM | HGB | HGB vs EWMA |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020-21 | selection | 24,800 | 5.130 | 5.040 | 4.858 | 4.839 | 4.786 | 4.787 | −5.0% |
| 2021-22 | selection | 27,919 | 5.307 | 5.183 | 4.997 | 4.941 | 4.894 | 4.907 | −5.3% |
| 2022-23 | selection | 27,654 | 5.304 | 5.187 | 4.989 | 4.913 | 4.858 | 4.869 | −6.1% |
| 2023-24 | selection | 28,131 | 5.160 | 5.067 | 4.902 | 4.831 | 4.783 | 4.789 | −5.5% |
| 2024-25 | test | 28,161 | 5.169 | 5.065 | 4.889 | 4.816 | 4.760 | 4.755 | −6.1% |
| 2025-26 | test | 28,333 | 5.139 | 5.054 | 4.878 | 4.774 | 4.720 | 4.729 | −6.4% |

The ranking is the same in every season. The test seasons did not degrade
relative to selection.

Pooled selection folds (108,504 rows): EWMA 5.121 / 6.708, ridge 4.939 /
6.430, random forest 4.882 / 6.336, LightGBM 4.831 / 6.276, HGB 4.839 /
6.286 (MAE / RMSE).

### Season holdout (trained through 2023-24, frozen)

| | 2024-25 MAE | 2024-25 RMSE | 2025-26 MAE (a year stale) | 2025-26 RMSE |
|---|---:|---:|---:|---:|
| Last 5 mean | 5.169 | 6.873 | 5.139 | 6.770 |
| EWMA | 5.065 | 6.689 | 5.054 | 6.627 |
| Ridge | 4.889 | 6.416 | 4.876 | 6.372 |
| Random forest | 4.816 | 6.293 | 4.786 | 6.237 |
| LightGBM | 4.760 | 6.230 | 4.737 | 6.187 |
| **HGB** | **4.755** | **6.231** | **4.739** | **6.191** |

A model a year out of date loses essentially nothing, so the relationship is
stable over time.

### Unconditional (DNP counted as 0 minutes), test seasons, 69,132 rows

EWMA MAE 6.461 (bias +2.29), HGB 6.021 (bias +2.23). Every model
overpredicts by about 2.2 minutes, because it is predicting minutes **if he
plays**. Telling "plays" from "sits" is exactly what V1 cannot do without
availability evidence. This is the gap Model V2 is meant to close.

## Selected Model V1: HistGradientBoostingRegressor (squared error)

- LightGBM had the lowest pooled selection MAE (4.831). HGB was 0.008 behind,
  well inside the 0.02 tie tolerance. Under the pre-registered tie-break the
  simpler option wins. Here that means scikit-learn's HGB: one fewer
  library, deterministic, and the same accuracy. The test seasons confirmed
  the tie (4.740 vs 4.742, CI of the difference spans 0).
- The absolute-loss HGB has lower MAE (4.715) but higher RMSE. It estimates
  the **median**, while the downstream stat models need **expected**
  minutes. It was excluded from selection in advance and is reported only for
  reference.
- Ridge is clearly worse (+0.14 MAE). The tree models capture interactions,
  e.g. "new team" × "previous minutes", that a linear model cannot.
- Serving model: hyperparameters tuned on 2025-26 (train through 2024-25):
  `max_leaf_nodes=31, max_iter=500, learning_rate=0.05,
  min_samples_leaf=50, l2=1.0`. It is fitted on all 215,746 eligible played
  rows from 2018-19 to 2025-26.

## Where it helps, and where it doesn't (test seasons; segments use pre-game information only)

| Segment | Rows | EWMA MAE | EWMA bias | HGB MAE | HGB bias | Gain |
|---|---:|---:|---:|---:|---:|---:|
| First game for a new team | 446 | 7.11 | +1.51 | 5.77 | +0.18 | 18.9% |
| Fewer than 10 games with new team | 3,321 | 5.97 | −0.70 | 5.07 | −0.11 | 15.1% |
| Returning after 5+ missed team games | 1,730 | 6.22 | +2.14 | 5.07 | +0.17 | 18.4% |
| First 5 games of a season | 5,413 | 5.85 | +0.83 | 4.87 | +0.12 | 16.7% |
| Playoffs | 3,725 | 5.37 | +1.06 | 4.61 | +0.26 | 14.1% |
| Play-in | 256 | 6.66 | +0.79 | 5.85 | −0.00 | 12.2% |
| Volatile role (SD of last 10 > 6.5, or < 3 games) | 17,630 | 6.17 | −0.09 | 5.47 | +0.06 | 11.2% |
| Medium stability | 30,694 | 4.73 | −0.03 | 4.55 | +0.10 | 3.8% |
| **Stable role (SD ≤ 3.5)** | 8,170 | 3.92 | −0.02 | 3.90 | +0.22 | **0.7%** |
| Bench last game | 30,213 | 5.48 | +0.02 | 5.10 | +0.01 | 6.9% |
| Started last game | 26,281 | 4.57 | −0.12 | 4.33 | +0.22 | 5.3% |
| Recent minutes < 15 | 13,532 | 5.75 | −1.06 | 5.35 | +0.10 | 6.9% |
| Recent minutes 15–25 | 18,377 | 5.41 | −0.01 | 5.03 | −0.05 | 7.0% |
| Recent minutes 25–32 | 14,912 | 4.69 | +0.29 | 4.43 | +0.20 | 5.4% |
| Recent minutes 32+ | 9,673 | 3.99 | +0.78 | 3.81 | +0.25 | 4.5% |
| Back-to-back | 9,486 | 5.29 | −0.80 | 5.04 | −0.01 | 4.6% |
| Rested | 47,008 | 5.01 | +0.11 | 4.68 | +0.13 | 6.6% |

The model mostly removes the baselines' **systematic biases**. Baselines
underestimate minutes after a long absence, early in a season and in the
playoffs. They overestimate for heavy-minute players, who regress toward the
mean, and on back-to-backs. For a player in a stable role, a recency-weighted
average is already about as good as V1.

## Error distribution and V1 uncertainty

Out-of-sample residuals (actual − predicted), walk-forward 2020-21..2025-26,
164,998 rows:

- SD 6.26, mean −0.10, skew +0.08, **excess kurtosis 1.24**. Jarque-Bera
  rejects normality decisively, and the tails are heavier than normal (1st/99th
  percentiles −15.4 / +16.1).
- **The spread depends on predicted minutes:** residual SD falls from about 7.1
  (predicted 10–18) to 5.1 (predicted 34+).
- **The shape is asymmetric for high-minute players.** At 34+ predicted, the
  median residual is +0.7 but the 10th percentile is −6.3. Big misses are
  mostly "played far less": injury exits, foul trouble, blowouts.
  Low-minute players have the opposite skew (median −2.0, 90th percentile +8.1).
- **Spread also depends on role stability:** SD 5.3 (stable), 6.0 (medium),
  7.1 (volatile).

V1 uncertainty is therefore **empirical residual quantiles by predicted-minutes
band**, not a normal distribution:

| Predicted minutes | Rows | Residual SD | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| < 10 | 16,919 | 6.13 | −6.3 | −4.5 | −2.0 | +2.2 | +8.1 |
| 10–18 | 35,746 | 7.12 | −9.1 | −5.3 | −0.6 | +4.1 | +9.2 |
| 18–24 | 32,591 | 6.61 | −8.1 | −4.1 | −0.1 | +4.2 | +8.5 |
| 24–30 | 35,721 | 6.10 | −7.6 | −3.9 | +0.2 | +3.9 | +7.3 |
| 30–34 | 27,182 | 5.49 | −6.9 | −3.0 | +0.6 | +3.5 | +6.0 |
| 34+ | 16,839 | 5.09 | −6.3 | −2.4 | +0.7 | +3.0 | +5.1 |

Calibration was tested out of sample. A table fitted on the selection folds
only, applied to the test seasons: the 80% interval covered **81.0%** and the
50% interval **51.5%**. A normal distribution with the same per-band SDs would
have covered 83.2% and 54.5%, so it is too wide in the middle and too narrow
in the tails. The serving model's table uses all walk-forward residuals.

So a prediction looks like: *expected minutes 33.4; 10th–90th percentile
26.5–39.4 minutes*.

## Failure cases

- **Overtime:** 4.3% of test rows, 12.3% of errors larger than 12 minutes,
  MAE 7.2 and bias −3.7. Players play more than predicted. No pregame model
  can know a game will go to overtime.
- **Cameos and early exits** (actual ≤ 5 minutes, 9.5% of rows): MAE 6.5,
  bias +6.5. These are mostly injuries during the game, garbage-time
  appearances and ejections.
- **Blowouts** (final margin ≥ 25) are surprisingly benign in aggregate (MAE 4.8
  vs 4.7), though players there are overpredicted on average (bias +1.2).
  Overtime and blowouts are identified from the game's final result, so this
  is analysis after the fact, never a feature.
- 5.7% of test predictions miss by more than 12 minutes, split roughly evenly
  between overpredictions (1,458) and underpredictions (1,736).
- **The data window:** no history before 2018-19, so "games of history" is
  capped at 82 and does not mean career experience.
- **Data quirk:** one team has two games dated 2020-03-01 in the source,
  which gives 11 rows with 0 rest days. Left as published.

## 2026-27 prospective predictions

`python -m app.nba.minutes.cli predict-upcoming [--hours-ahead 36] [--dry-run]`

- The information cutoff is the time of the run. Features come from the same
  builder, with `cutoff=now`, so a box score counts only if it was final at
  least 4 hours before the cutoff. Completed 2026-27 games ARE used as history:
  they are prior information at prediction time.
- Only scheduled games that have not tipped off are predicted. Each row is
  written once to `nba_minutes_predictions`, together with its information
  cutoff, model run id, expected minutes, quantiles, the exact input values
  and how the player was assigned to the team. The row is append-only, and
  changing it raises `FrozenRecordError`.
- **Who gets predicted:** each team's roster as last observed at or before the
  cutoff (`nba_team_observations`). This is the prospective equivalent of the
  historical box-score listing, and the only use of prospective evidence in
  V1. Without one, it uses players whose latest known box score was for that
  team. Roster players unknown to the database, or with no history, are
  counted and reported, not guessed.
- **V1 uses no injury or availability evidence.** A player listed as out still
  gets a "minutes if he plays" prediction.
- Nothing is settled or evaluated. The outcome doesn't exist yet.
- Verified on the local database (dry run, 2026-10-03, for the first three
  2026-27 regular-season games): 122 roster candidates, 95 predicted, 23
  roster players not yet in `nba_players`, 4 with no history.

**Not yet usable in production:** the hosted database holds no historical
box scores (by decision), so prospective features can't be computed there.
The new tables also need migration `47742ee9eb7a`, which has **not** been
applied to production. The live evidence cycle is unaffected while its image
stays pinned to code whose migration head is `90343a82a7c8`. But re-pinning it
to a commit containing this migration, without migrating first, would make the
cycle refuse to run (exit 3).

## Registry and reproducibility

- `nba_minutes_model_runs` (append-only): one row per baseline and candidate per
  experiment, plus one "serving" row. Each records:
  - code version (git SHA),
  - dataset cutoff,
  - training and evaluation seasons,
  - features,
  - per-fold hyperparameters,
  - metrics,
  - the uncertainty table,
  - the model file path and SHA-256.

  Loading a model verifies the hash.
- Model files and row-level walk-forward predictions/residuals are in
  `backend/model_artifacts/nba_minutes/`. They are gitignored and regenerated
  by `evaluate`. Model files are never overwritten.
- Each experiment writes a new timestamped JSON report to `docs/nba_minutes_v1/`.
- Deterministic: re-running from the same code gives identical metrics and a
  byte-identical model.

## Known limitations

1. Predictions are conditional on playing and are not constrained to a
   team-wide 240 minutes. With a 20-man preseason roster, the predicted minutes
   for a team sum to far more than 240.
2. No information about teammates' availability for game Y itself, only
   their absences in earlier box scores.
3. Players with no history in the window get only a constant fallback.
4. No coach or rotation-change information, and no pregame spread or total
   (no historical odds), so blowout risk comes only from team point
   differentials.
