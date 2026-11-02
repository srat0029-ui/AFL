# NBA Minutes V2: plan and pre-registered evaluation

Written 2026-10-30, **before** any 2026-27 outcome or the evidence audit was
examined. V1 (E(minutes | plays)) and V1.5 (P(play), P(10+)) stay exactly as
they are in production. V2 is a separate, labelled layer and is adopted only
if it passes the rules below.

## Why V2, and why this design

V1.5's errors are availability errors (see NBA_ROTATION_MODEL_V1.md §3, §12):
short absences are the weakest segment (log loss 0.56, AUC 0.74); 1,423
test-season players with P(play) ≥ 0.9 never dressed; reconciliation to 240
team minutes only helps once the active list is known (−0.12 MAE).

There is **no historical pregame injury evidence** (collection started
2 Oct 2026, with an outage 9–28 Oct). So V2 is factorised so that only a
small part depends on it:

    P(play) = P(dresses | evidence, history) × P(plays | dresses, history)

- P(plays | dresses) and E(minutes | dresses, who else dressed) can be trained
  on eight seasons, because the box-score listing is the list of who dressed.
- P(dresses | evidence) is a low-dimensional recalibration of V1.5 by
  injury-feed state, learned from frozen 2026-27 snapshots.

## Step 0: read-only evidence audit

`.github/workflows/nba-evidence-audit.yml` (manual, read-only, aggregates
only). Questions it answers:

1. Which statuses does ESPN's feed publish in season (Out / Questionable /
   Doubtful / Probable, or only Out / Day-To-Day)?
2. Do starters, and a shrunken active list, appear in the per-game roster
   before tip-off, and how long before?
3. At each snapshot, how does feed state relate to dressing and playing?
4. How many players play without having had an outlook?

The games it summarises are **design data**. They are never V2's test set.

## Step 1: offline upper bound (local database only)

**Question:** if the active list (who dressed) were known perfectly, how much
would participation and minutes accuracy improve? If even perfect knowledge
gains little, V2's minutes part is not built.

- Data and protocol identical to V1 / V1.5: seasons 2018-19..2025-26,
  season walk-forward, nested tuning, selection folds 2020-21..2023-24, test
  folds 2024-25 and 2025-26, the roster universe as candidates.
- Scenarios, scored on identical rows:
  - **A** V1.5 as served (P(play) roster model; raw V1 minutes).
  - **B** Active list known: P(play) = 0 if not dressed, else V1.5's
    listed-scenario P(play | dressed). Minutes unchanged.
  - **C** B + a minutes model retrained with teammate-absence features for
    game Y, computed from the roster candidates who did not dress: their
    recent minutes summed, how many, how many regular starters, and the
    player's minutes rank among those who dressed.
  - **D** C + variance-weighted reconciliation to 240 over the dressed set
    (α chosen on selection folds only).
- The oracle (game Y's listing) is used deliberately and labelled; every
  other input stays as-of, 4 h before tip-off, as in V1.
- Metrics: conditional minutes MAE / RMSE / bias on played rows;
  unconditional expected minutes (P(play) × E(minutes), DNP = 0) MAE;
  P(play) log loss / Brier / AUC / ECE. Segments: predicted-minutes band, and
  rows with at least 15 recent minutes of absent teammates.
- **Decision rule, fixed now:** build V2's minutes part only if C or D beats
  A on test-season conditional MAE by at least 0.10 minutes with a 95%
  paired-bootstrap CI (clustered by game date, 2,000 resamples) excluding 0,
  and the selection folds agree in sign. Otherwise report the negative
  result and keep V1 minutes.

## Steps 2–3: prospective V2 on frozen 2026-27 snapshots

- **Design period:** games tipping off before **2026-12-01T00:00Z**. Used to
  choose features, priors and the recalibration form.
- **Test period:** games tipping off on or after 2026-12-01T00:00Z, scored no
  earlier than when at least 300 final games with a produced T-1h snapshot
  exist in it. Nothing about V2 changes once the test period is looked at.
- Comparators, on identical candidates and snapshots: V1.5 as frozen, and
  the simple rule "listed Out → P(play) = 0, otherwise V1.5" (V2 must beat
  this, not only V1.5).
- Primary metric: P(play) log loss at T-1h over all candidates. Secondary:
  Brier, ECE, the injury-listed subset, every snapshot window, unconditional
  expected-minutes MAE, and conditional minutes MAE if step 1 passed.
- **Adoption rule:** primary metric better than both comparators with a 95%
  paired-bootstrap CI (clustered by game date) excluding 0, and no window
  with significantly worse calibration. A failure is reported, not tuned
  away on the test period.

## Amendment 1 (2026-10-30, after step 1 and the audit; before step 1b ran)

Step 1 passed under an oracle. The audit showed the oracle is not available
at T-1h (the per-game list is ~90% precise, the feed has only Out /
Day-To-Day). So V2 cannot serve "who dressed"; it must serve **P(dresses)**,
and the minutes model must take teammate absences as probabilities.

**V2 composition:**
1. P(dress | history): roster-universe classifier, target = listed.
2. P(dress | history, evidence): recalibration of (1) by injury-feed state,
   fitted on design-period snapshots (step 2). Before that, (1) alone.
3. P(play | dressed, history): the listed-scenario classifier.
4. P(play) = (2) × (3).
5. E(minutes | plays): the step-1 model C's features, computed as
   **expectations** over teammates' P(dress) q_j (with q_j ∈ {0, 1} they
   equal the oracle features exactly).

**Step 1b — history-only V2, offline (rule fixed before running):** with
q_j = P(dress | history) only (no evidence, so fully reconstructable), on
the step-1 rows and folds:
- **E-hist**: minutes from C trained on oracle features, served with
  expected features. **F-hist**: the same model form trained on the expected
  features themselves (train = serve).
- Participation: P(play) = q × P(play | dressed) vs V1.5's direct P(play).
- **Rule:** the history-only minutes model is a candidate to replace V1
  inside V2 if E-hist or F-hist beats A on test conditional MAE with a 95%
  CI excluding 0 and the selection folds agree in sign (no minimum size:
  any reliable gain without evidence is free). The factorised P(play) is
  kept only if its test log loss is no worse than V1.5's by more than 0.002
  (the V1.5 tie tolerance); otherwise V2 recalibrates V1.5's P(play)
  directly. Neither result changes steps 2–3's pre-registered rules.

## Not planned

More model complexity for V1 (LightGBM tied HGB), overtime modelling (no
better than a constant), changes to V1's uncertainty bands. None addresses
the availability errors.
