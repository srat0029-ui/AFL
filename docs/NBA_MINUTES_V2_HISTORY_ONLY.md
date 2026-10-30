# Minutes V2, step 1b: history-only V2 — results (negative for minutes)

Rule: [NBA_MINUTES_V2_PLAN.md](NBA_MINUTES_V2_PLAN.md), amendment 1, pushed
(6ec61d7) before the code existed. Report:
`docs/nba_minutes_v2/history-only-20261030T062945Z.json` (code f5337a2).
Local database only; nothing served or registered.

**Question:** without any evidence, can V2 already help, using each
teammate's P(dress | history) in place of who actually dressed?

## Minutes: no — fails the rule

Test seasons 2024-25 + 2025-26, conditional minutes, 56,249 played rows:

| | MAE | RMSE | Bias | vs A (95% CI) | Selection vs A |
|---|---:|---:|---:|---|---|
| A  V1 | 4.736 | 6.198 | +0.11 | | |
| C  oracle (step 1, reference) | 4.568 | 5.965 | +0.07 | +0.168 [0.144, 0.196] | +0.151 |
| E-hist (C model, expected features) | 4.730 | 6.248 | **−0.32** | +0.006 [−0.008, 0.020] | +0.020 |
| F-hist (trained on expected features) | 4.735 | 6.195 | +0.08 | +0.000 [−0.008, 0.009] | **−0.022** |

- Neither passes. E-hist's CI spans 0 and it is biased low: the oracle-
  trained model reads "teammates probably absent" as "teammates absent",
  which history alone cannot know. F-hist (train = serve) recovers the bias
  but has nothing to learn: it ties V1 and is worse on selection.
- So **the step-1 minutes gain comes entirely from knowing about new
  absences**, which history cannot predict. Whatever V2 minutes gains will
  come from injury evidence, and only to the extent that evidence
  identifies who will be missing. History-only V2 minutes is not adopted;
  V1 stays the minutes model until evidence is in.

## Participation: the factorised form is kept

| Test (118,724 roster candidates) | Log loss | Brier | AUC | ECE |
|---|---:|---:|---:|---:|
| V1.5 direct P(play) | 0.2917 | 0.0900 | 0.9457 | 0.0059 |
| q × P(play \| dressed) | **0.2912** | 0.0899 | 0.9459 | 0.0056 |
| P(dress \| history), vs dressed | 0.2150 | 0.0603 | 0.9642 | 0.0059 |

The factorised P(play) is as good as V1.5's (within the 0.002 tolerance;
selection 0.2806 vs 0.2807), so V2 keeps the factorised form. That puts
the injury evidence where it belongs: on P(dress), which the Out /
Day-To-Day feed speaks to directly, with P(play | dressed) unchanged.

Unconditional expected minutes (DNP = 0): A 4.155, factorised × E 4.128,
factorised × F 4.154 — no material change without evidence.

## What this means for V2

1. The value of V2 lies between 0 (this result) and step 1's oracle
   (+0.17 conditional MAE; P(play) log loss 0.292 → 0.179), set by how well
   evidence at the cutoff identifies who will not dress.
2. The audit's first look (6 team-games) puts the per-game list at ~90%
   precision and 92–97% recall at T-1h / T-30m, and Out as decisive. That
   is promising but must be measured on design-period data before any
   claim.
3. Step 2 proceeds as pre-registered: recalibrate P(dress | history) by
   evidence state (`minutes_v2/participation.py`), fitted on games before
   2026-12-01, judged on games after; minutes use expected teammate
   features under the evidence-adjusted q only if that passes too.
