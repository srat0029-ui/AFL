# Minutes V2, step 1: offline upper bound — results

Protocol and decision rule: [NBA_MINUTES_V2_PLAN.md](NBA_MINUTES_V2_PLAN.md),
fixed before this ran. Report:
`docs/nba_minutes_v2/upper-bound-20261030T053939Z.json` (code
`app/nba/minutes_v2/upper_bound.py`, commit 18c9eaf, later rebased unchanged
as 33b96ff). Local database only; nothing served or registered.

**This is an upper bound.** Scenarios B–D know exactly who dressed for game Y
(the box-score listing). Before tip-off that is known only as far as the
injury feed and the active list reveal it. Real V2 gains lie between A and
these numbers.

## Decision: passes — build V2's minutes part

Test seasons 2024-25 + 2025-26, conditional minutes on 56,249 played
roster-universe rows (all scenarios on identical rows):

| Scenario | MAE | RMSE | Bias |
|---|---:|---:|---:|
| A  V1 as served | 4.736 | 6.198 | +0.11 |
| A' control (roster-trained, V1 features) | 4.734 | 6.196 | +0.10 |
| C  + teammate-absence features | 4.568 | 5.965 | +0.07 |
| D  C reconciled to 240 (α = 0.25, chosen on selection) | **4.557** | **5.951** | +0.07 |

Paired difference in absolute error vs A (positive = better), 95% CI
clustered by game date:

| | Test | Selection |
|---|---|---|
| C vs A | 0.168 [0.144, 0.196] | 0.151 [0.138, 0.166] |
| D vs A | 0.178 [0.154, 0.208] | 0.184 [0.167, 0.203] (2021–23) |
| C vs A' (feature effect only) | 0.165 [0.142, 0.193] | 0.149 [0.136, 0.164] |
| A' vs A (population effect) | 0.002 [−0.002, 0.007] | 0.002 [−0.001, 0.006] |

Both C and D clear the ≥ 0.10-minute rule with CIs excluding 0, and the
selection folds agree. The control shows the gain is the teammate features,
not the change of training rows. Reconciliation adds only ~0.01 on top of C.

## Participation and unconditional minutes (test, 118,724 roster candidates)

| | P(play) log loss | Brier | AUC | ECE | E[minutes] MAE (DNP = 0) |
|---|---:|---:|---:|---:|---:|
| A  V1.5 | 0.292 | 0.090 | 0.946 | 0.006 | 4.155 |
| B  active list known | 0.179 | 0.057 | 0.977 | 0.003 | 3.090 |
| C | | | | | 2.993 |
| D | | | | | 2.986 |

Knowing who dresses is by far the larger lever: it cuts unconditional
expected-minutes error by ~26%, against a further ~3% from better
conditional minutes. Participation evidence (step 2) is the priority.

## Where the minutes gain is — and is not

Test MAE by V1 predicted-minutes band:

| Band | Rows | A | C | D |
|---|---:|---:|---:|---:|
| < 10 | 6,155 | 4.59 | 4.27 | 4.27 |
| 10–20 | 15,553 | 5.55 | 5.24 | 5.22 |
| 20–25 | 9,676 | 4.94 | 4.74 | 4.73 |
| 25–30 | 10,661 | 4.55 | 4.49 | 4.47 |
| 30–34 | 8,993 | 4.07 | 4.06 | 4.05 |
| 34+ | 5,211 | **3.64** | **3.66** | **3.66** |

The gain is concentrated in bench and fringe-rotation players, whose minutes
absorb a teammate's absence. For 30+-minute players — the core of most
player-prop markets — it is negligible, and slightly negative at 34+. V2's
minutes improvement will matter most for secondary players' props.

## Caveats

- Oracle information: real gains depend on how early and how completely the
  injury feed and active list reveal absences (step 0 audit).
- The pre-registered segment "≥ 15 recent minutes of absent teammates" turned
  out not to discriminate: 42,216 of 56,249 test rows fall in it, because the
  roster universe carries several non-dressed players per team. Reported as
  registered (A 4.83 → D 4.65; others 4.45 → 4.28); not re-cut after seeing it.
- C has a slightly higher selection-fold bias (+0.18 vs +0.12).
- Unforeseen newcomers (1.2% of listings) are outside the roster universe, so
  reconciliation's 240 budget ignores their minutes.
