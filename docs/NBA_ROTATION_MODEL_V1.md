# NBA Rotation Layer V1 ("V1.5")

Expected Minutes V1 estimates **E(minutes | player plays)** and keeps that
meaning. This layer adds, as **separate** quantities:

| Quantity | Meaning | Status |
|---|---|---|
| `p_play` | P(he enters the game) | learned, selected |
| `p_rotation` | P(he plays ≥ 10 minutes); capped at `p_play` | learned, selected |
| `expected_minutes_if_plays` | V1, unchanged | — |
| `reconciled_minutes_if_plays` | team-minutes reconciled conditional minutes | **evaluated, NOT adopted** |
| quantiles, tier, raw availability evidence, experimental rules | see below | exposed |

They are never multiplied together. A player prop is void if the player does
not play, so pricing needs E(minutes | plays) and P(play) as two numbers.

Canonical run: `docs/nba_rotation_v1/report-20261004T050400Z.json` (code
`ed4955a`). `report-20261004T041928Z.json` is an identical run made
before the code was committed: 0 metric differences and identical model
files. Run it: `python -m app.nba.rotation.cli evaluate` (~45 min).
Upcoming outlooks: `python -m app.nba.rotation.cli predict-upcoming [--dry-run]`.

## The most important finding: the box-score listing is availability information

The first version of this work used "players listed in the game's box score"
as the candidate universe. That turned out to be leakage, and it made
reconciliation look much better than it is.

ESPN box scores list the players who **dressed**, about 13 per team. Listings
per team-game: 11 (1,521), 12 (5,765), 13 (7,056), 14 (3,189), 15 (2,778),
with only a handful above 15. That is essentially the official active list,
published about 30 minutes before tip-off together with the inactive list.
Most injured players are simply not listed. There are only 0.35
injury/rest-type DNP listings per team-game, against the 2–3 inactive players
a typical team carries.

Conditioning on the listing therefore means knowing who is inactive, which is
availability information. V1.5 is defined as historically reconstructable
information only, so:

- **Primary universe ("roster")**: the pregame roster reconstructed from
  history alone. A player is a candidate for team T's game if his latest
  box-score listing known at the cutoff was for T, in this season or the
  previous one (`app/nba/rotation/universe.py`). Injured players stay
  candidates, and simply don't play. A player's first-ever listing for a team
  is not foreseeable from history: that covers debuts, signings, and trades
  not yet seen in any box score. Those listings are 3,062 rows (1.2% of
  listings, 0.7% of all minutes); prospectively, the roster observation covers
  them.
- **Scenario ("listed", labelled)**: the box-score listing. It is evaluated
  only as an "active list known" upper bound, the kind of information Minutes
  V2 will add. It is never used for serving.

The earlier listed-universe run is kept, as superseded, in
`docs/nba_rotation_v1/report-20261004T035925Z.json`. Its participation numbers
are this document's "listed scenario".

## 1. Participation dataset and class balance (2018-19..2025-26)

| | Roster universe (primary) | Listed scenario |
|---|---:|---:|
| Candidate rows | 437,503 | 264,966 |
| Candidates per team-game | 21.4 | 13.0 |
| Played (records minutes) | 49.2% | 82.0% |
| Played ≥ 10 min | 42.0% | 70.0% |
| Played ≥ 5 min | 45.6% | 75.9% |
| Dressed (listed) | 59.8% | 100% |
| No earlier played game (no history) | 7,433 rows | 2,497 rows |

Labels are outcomes only:
- `y_play`: entered the game. Not listed, or listed did-not-play, counts as 0.
- `y_rot10` and `y_rot5`: NaN when the player played but no minutes were
  recorded (256 rows); those rows are never guessed.

## 2. Candidates and baselines

**Features:** V1's 41 pregame features, plus seven built from the player's
listings:
- share of his last 5 listings played,
- mean minutes over his last 5/10 listings with DNPs counted as 0,
- share of his last 10 listings with 10+ minutes,
- listings known,
- **team games since he was last listed** (an inactive, injured or departed
  streak, read from the schedule),
- a no-history flag.

DNP reasons are never used.

**Baselines,** each fitted on training rows only:
- the training base rate,
- the empirical rate by previous-listing state (none / DNP / cameo / 10+),
- the player's own recent play rate (or 10+ rate), calibrated with a
  one-feature logistic.

**Candidates:** logistic regression (imputation, missing indicators, scaling),
HistGradientBoostingClassifier, LightGBM. The protocol is identical to V1:
season walk-forward, nested tuning on log loss, selection folds
2020-21..2023-24, test folds 2024-25 and 2025-26.

## 3. Walk-forward classification results (test seasons 2024-25 + 2025-26)

### P(play), roster universe (118,724 candidate rows, 47.5% played)

| Model | Log loss | Brier | AUC | ECE |
|---|---:|---:|---:|---:|
| Base rate | 0.693 | 0.250 | 0.49 | 0.021 |
| Previous-listing state | 0.587 | 0.199 | 0.731 | 0.036 |
| Recent play rate (calibrated) | 0.585 | 0.199 | 0.759 | 0.037 |
| Logistic | 0.327 | 0.101 | 0.935 | 0.024 |
| LightGBM | 0.291 | 0.090 | 0.946 | 0.005 |
| **HGB (selected)** | **0.292** | **0.090** | **0.946** | **0.006** |

The roster universe includes easy negatives: players who left, or who have
been out for weeks. **On recently-listed candidates only** (listed in one of
their team's last 3 games: 77,502 rows, 71.3% played), HGB scores
log loss 0.399, AUC 0.867, ECE 0.009, against 0.578 / 0.760 for the
recent-play-rate baseline.

**Thresholds** (HGB, all roster candidates, test seasons):

| Threshold | Precision | Recall | Specificity | NPV |
|---|---:|---:|---:|---:|
| 0.3 | 0.788 | 0.953 | 0.768 | 0.948 |
| 0.5 | 0.867 | 0.868 | 0.880 | 0.881 |
| 0.7 | 0.908 | 0.786 | 0.928 | 0.827 |
| 0.9 | 0.940 | 0.603 | 0.965 | 0.729 |

### Calibration (HGB P(play), test seasons, predicted vs observed)

| Bin | n | mean p | observed |
|---|---:|---:|---:|
| 0.0–0.1 | 40,976 | 0.018 | 0.019 |
| 0.1–0.2 | 5,523 | 0.143 | 0.153 |
| 0.2–0.3 | 4,062 | 0.251 | 0.250 |
| 0.3–0.4 | 5,641 | 0.352 | 0.360 |
| 0.4–0.5 | 6,134 | 0.449 | 0.453 |
| 0.5–0.6 | 4,414 | 0.546 | 0.569 |
| 0.6–0.7 | 3,211 | 0.649 | 0.661 |
| 0.7–0.8 | 3,681 | 0.754 | 0.748 |
| 0.8–0.9 | 8,929 | 0.862 | 0.845 |
| 0.9–1.0 | 36,153 | 0.946 | 0.940 |

Well calibrated across the whole range (ECE 0.006).

**Where it is hard** (HGB log loss / AUC, test seasons):

| Segment | Rows | Played | Log loss | AUC |
|---|---:|---:|---:|---:|
| Missed 1–4 team games | 18,506 | 35.7% | 0.562 | 0.742 |
| Unlisted for 1–2 team games | 7,848 | 26.5% | 0.468 | 0.789 |
| Unlisted for 3–9 team games | 9,850 | 7.9% | 0.249 | 0.729 |
| Listed in team's last game | 69,654 | 76.4% | 0.391 | 0.846 |
| No history | 1,868 | 4.6% | 0.136 | 0.888 |

Short absences are the hard part: will the player who sat the last game or
two be back? That is exactly where injury and availability evidence (V2)
should add the most.

### Listed scenario (active list known): P(play | dressed)

HGB log loss 0.311, AUC 0.885, ECE 0.006 (baseline 0.370 / 0.829). Among
players who dressed, the remaining DNPs are coach's decisions.

## 4–5. Meaningful rotation: why ≥ 10 minutes, and the comparison with ≥ 5

The threshold was chosen for what it means on the court, not for its metrics:
- The played-minutes histogram has its trough at 8–10 minutes, between a
  cameo / garbage-time mode below about 6 minutes and the rotation mode above 12.
- About 9.1 players per team-game reach 10 minutes, which is a normal rotation.
- Below 10 minutes, a player's minutes depend on game state (blowouts, foul
  trouble), not on a planned role.

| Target (roster, test) | Rate | HGB log loss | AUC | ECE | Recently listed: log loss / AUC |
|---|---:|---:|---:|---:|---:|
| Plays (> 0) | 47.5% | 0.292 | 0.946 | 0.006 | 0.399 / 0.867 |
| ≥ 5 minutes | 43.7% | 0.274 | 0.952 | 0.006 | 0.381 / 0.894 |
| **≥ 10 minutes** | 40.2% | **0.263** | **0.955** | 0.005 | **0.370 / 0.907** |

Both thresholds are predictable. ≥ 10 is kept because it separates genuine
rotation players from emergency and cameo minutes. Baselines for ≥ 10: recent
10+ rate 0.477 / 0.845, previous-listing state 0.513 / 0.778.

**Coherence:** "plays 10+" implies "plays", but two separate classifiers can
disagree. On the 2025-26 fold, P(10+) exceeded P(play) on 14.5% of rows, by
0.016 on average. Served outlooks therefore cap `p_rotation` at `p_play`,
which very slightly improves rotation log loss (0.27407 → 0.27403). The
evaluation tables above are uncapped.

## 6. Selected participation models

HGB for both P(play) and P(10+). LightGBM was within the pre-registered
0.002 log-loss tie (0.2795 vs 0.2807 for P(play), 0.2490 vs 0.2500 for
P(10+)), so the simpler, deterministic scikit-learn model wins. Logistic
regression was clearly worse (0.315 / 0.279). Serving models are fitted on
all of 2018-19 to 2025-26.

## 7. Candidate universe and tiers (prospective)

Candidates for an upcoming game:
- **With a roster observation:** the team's roster as last observed at or
  before the cutoff, i.e. `rostered`.
- **Without one:** players whose latest known box score was for that team, in
  this season or the last. This mirrors the historical reconstruction.

Roster players unknown to the database are reported, not guessed. Players
with no history get `p_play` and `p_rotation`, which the models estimate for
such rows (historically 4.6% of them played), but **no minutes**:
`expected_minutes_if_plays` is null.

Tiers are a label derived from the calibrated probabilities. They are never a
substitute for them:

| Tier | Rule |
|---|---|
| likely_rotation | p_rotation ≥ 0.7 |
| likely_active | p_play ≥ 0.8 (and not likely_rotation) |
| unlikely | p_play < 0.3 |
| uncertain | otherwise |

**What a p_rotation filter does** (test seasons, roster universe):

| p_rotation ≥ | Players per team-game | Precision for actual 10+ | Recall of actual 10+ | V1 MAE inside | V1 MAE outside |
|---|---:|---:|---:|---:|---:|
| 0.5 | 9.3 | 0.859 | 0.882 | 4.59 | 5.27 |
| 0.7 | 8.2 | 0.892 | 0.809 | 4.50 | 5.32 |
| 0.9 | 5.4 | 0.936 | 0.558 | 4.27 | 5.16 |

Filtering changes **which** players are priced, not their conditional
minutes. Players outside the likely rotation have worse minutes predictions.

## 8. Raw V1 vs team-minutes reconciliation (B / C)

The reconciliation is a variance-weighted projection: the smallest change,
weighted by each player's V1 residual variance, that closes a fraction α of
the gap between the team's expected total, Σ P(play)·E(minutes | play), and
the budget. Confident high-minute predictions move least. A proportional
variant was also tested, with budgets of strict 240 or 240 plus an
overtime expectation. The configuration was chosen on selection folds
2021-22..2023-24 and adopted only if it beat raw V1 by more than 0.01 minutes
of MAE without worsening RMSE.

Conditional-minutes MAE on players who played (strict 240 budget):

| | Roster universe: selection MAE | Roster universe: test MAE | Listed scenario: selection MAE | Listed scenario: test MAE |
|---|---:|---:|---:|---:|
| A. Raw V1 | **4.849** | **4.736** | 4.868 | 4.750 |
| C. Variance-weighted, α = 0.25 | 4.863 | 4.734 | 4.787 | 4.677 |
| C. Variance-weighted, α = 0.5 | 4.879 | 4.752 | 4.741 | 4.636 |
| C. Variance-weighted, α = 0.75 | 4.900 | 4.782 | 4.730 | 4.627 |
| C. Variance-weighted, α = 1 | 4.928 | 4.819 | 4.756 | 4.650 |
| C. Proportional, α = 0.5 | 4.881 | 4.754 | 4.790 | 4.681 |

**Over the honestly reconstructed roster, reconciliation does not help.**
Every α > 0 is worse on the selection folds, so it is **not adopted**, and
`reconciled_minutes_if_plays` stays null. It only helps when the active list
is known (−0.12 on test). That is because knowing who dressed is exactly
what fixes the team's minute pool. Reconciliation therefore belongs in
**Minutes V2**, once the official inactive list or reliable injury evidence
is available before the cutoff.

(Raw V1 MAE here is 4.736, not V1's 4.742, because the populations differ:
roster candidates who played. "Unforeseen" players are excluded, and
no-history players are included with the 9-minute fallback.)

## 9. Team-minute totals

Expected team totals (Σ p·m), test seasons:

| | Mean expected | Mean actual | Mean error | MAE | 5th–95th percentile |
|---|---:|---:|---:|---:|---:|
| Roster universe, raw | 241.2 | 239.0 | +2.3 | 10.7 | 225–259 |
| Listed scenario, raw | 242.8 | 241.4 | +1.4 | 16.3 | 208–272 |
| Listed scenario, reconciled | 241.9 | 241.4 | +0.4 | 5.4 | 233–249 |

On the roster universe, actual minutes average 239.0, not 241.4. The
difference is minutes played by unforeseen newcomers, who are outside the
universe. The raw expected total is unbiased to within about 2 minutes. Its
game-to-game error (10.7) is what a correct active list would remove.

## 10. Overtime

- Overtime happens in 5.3% of games (4.8% in the test seasons), adding 1.13
  periods on average. The expected budget is therefore about 241.5 team
  minutes, against a strict 240.
- A pregame model using matchup closeness (shrunk point-differential gap)
  does not beat the constant rate. Brier scores are equal to the fourth
  decimal in every fold.
- In the listed scenario, where reconciliation matters, the OT-adjusted budget
  vs strict 240 changes test MAE by 0.002 (4.625 vs 4.627).
- **The effect is too small to matter, so the simpler strict-240 design
  stands.** V1's overtime underprediction (bias −3.7 minutes in OT games) is
  a property of the outcome, not something a pregame budget can fix.

## 11. Performance by predicted-minutes band (V1, test seasons, played rows; bands from PREDICTED minutes)

| V1 predicted minutes | Rows | V1 MAE | V1 RMSE | V1 bias | Within ±4 | EWMA MAE | Mean P(play) | Mean P(10+) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| < 10 | 6,155 | 4.59 | 6.10 | +0.17 | 52.8% | 5.12 | 0.42 | 0.12 |
| 10–20 | 15,553 | 5.55 | 7.05 | +0.04 | 44.8% | 5.94 | 0.79 | 0.63 |
| 20–25 | 9,676 | 4.94 | 6.34 | −0.12 | 49.8% | 5.30 | 0.89 | 0.86 |
| 25–30 | 10,661 | 4.55 | 5.88 | +0.20 | 53.1% | 4.81 | 0.90 | 0.89 |
| 30–34 | 8,993 | 4.07 | 5.47 | +0.26 | 60.0% | 4.20 | 0.90 | 0.90 |
| 34+ | 5,211 | 3.64 | 5.06 | +0.22 | 66.4% | 3.79 | 0.92 | 0.92 |

**Players bookmakers typically offer main props for** are regular rotation
players, roughly 20+ predicted minutes. For them V1's conditional MAE is
3.6–4.9 minutes, and 50–66% of predictions land within 4 minutes. Players with
p_rotation ≥ 0.8 (36,336 test rows) have MAE 4.42 and RMSE 5.82, with 55.8%
within 4 minutes. The 10–20-minute band is the least predictable (MAE 5.5),
and V1's gain over EWMA shrinks toward the top of the minutes range.

## 12. Difficult and failure cases (test seasons, roster universe, 118,724 candidates)

- **Confident "plays" that did not:** 2,177 had P(play) ≥ 0.9 but didn't play.
  1,423 of them didn't even dress: late scratches, rest days, new injuries,
  all invisible to history. That is the V2 target.
- **Confident "sits" that played:** 1,620 had P(play) ≤ 0.2 and played
  (unexpected returns, emergency minutes).
- **P(10+) ≥ 0.9 but under 10 minutes:** 1,815 (early exits, blowouts,
  injuries during the game).
- **Short absences** (missed 1–4 team games) are the weakest segment
  (log loss 0.56, AUC 0.74).
- **Unforeseen newcomers** (first listing for a team) can't be candidates
  historically. Raw V1 on the 446 who played in the test seasons: MAE 6.59.
- **Season openers:** the historical roster carries last season's players
  until they appear elsewhere. Prospectively the roster observation is
  better, but the historical evaluation is somewhat pessimistic early in a
  season.
- **Observed prospectively (dry run, 2026-10-04, first three 2026-27
  games):** the team-level expected totals Σ P(play)·E(minutes | play) are
  only 145–203, well below about 240. Two causes:
  - The model learned at season starts from candidate pools that include
    departed players, so its P(play) is pessimistic for players a CURRENT
    roster observation confirms are on the team.
  - 23 roster players are not yet in `nba_players` and are not predicted.

  This hasn't been measured historically (there were no roster observations
  then). It is a concrete reason to evaluate 2026-27 opener calibration
  first, and to treat roster confirmation as evidence in V2.

## 13. How prospective availability is exposed

Each outlook stores `availability_evidence`, the **raw** injury-feed state
known at the cutoff (`app.nba.asof.availability_known_at`):

- `feed_not_read_by_cutoff`: we had not read the feed by the cutoff.
- `never_listed`: the feed has never listed him. This is "no evidence", and
  **not** evidence of health.
- `no_longer_listed`: the feed stopped listing him. No status is implied.
- `listed`: with status, source status, the first time that state was
  observed, injury details and expected return date.

`experimental_rules` holds separately labelled prospective rules. Currently
`experimental_out_status_v0`: if the feed lists him as Out at the cutoff, the
rule's P(play) is 0, with `applied_to_learned_fields: false`. These rules are
not learned and not historically validated (there is no history of this
evidence yet), and they **never** change `p_play`, `p_rotation` or the minutes
fields. A test asserts this. Minutes V2 will be a separate, labelled layer
evaluated on the 2026-27 frozen outlooks, without redefining V1 or V1.5.

## 14. Leakage protections

- Every feature goes through V1's as-of builder: a box score counts only if
  final and tipped off at least 4 hours before the cutoff. The seven new
  features use the same as-of join.
- Roster membership is decided from listings known at the cutoff only.
- Labels (listed / played / minutes) are never features. DNP reasons are never
  used, and nothing from the target game's box score feeds anything,
  including its starter flag.
- All preprocessing is fitted on training rows only. Baselines are fitted on
  training rows too.
- **Proof on real data:** every run checks 40 stratified real candidate rows
  (random, not dressed, no history, traded, long-unlisted). Their V1 features,
  participation features **and roster membership** are recomputed from the
  database truncated at each row's cutoff. Result: **0 differences**.
- Prospective: candidates come from the roster observed at or before the
  cutoff, features from box scores known by then, and availability evidence
  from observations at or before the cutoff. Rows are frozen before tip-off,
  and a row can't be edited (`FrozenRecordError`).

## 15. Migration

`c133d5a7a11e` adds the append-only `nba_rotation_predictions` table, after
V1's `47742ee9eb7a`. Neither has been applied to production. The hosted
database is still at `90343a82a7c8`, and the live-cycle image is still
pinned to code with that head. Re-pinning the image past these migrations
without migrating first would make the cycle refuse to run (exit 3).

## Recommendation

1. **Minutes V2 = V1.5 + prospective availability evidence**, built and
   evaluated on frozen 2026-27 outlooks once games are played:
   - injury-feed status (Out / Doubtful / Questionable),
   - the official active / inactive list and lineup observations as they
     become available before tip-off,
   - then reconciliation, which the listed scenario shows is worth about
     0.12 minutes of MAE once the active list is known.

   Start freezing V1.5 outlooks now so that V2 has a prospective baseline to
   beat.
2. Then PTS/REB/AST per-minute rate models on top of E(minutes | plays), with
   P(play) kept separate for void handling.
