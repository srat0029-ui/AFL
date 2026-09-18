# AFL v1 Release — Freeze Checkpoint

**Release date:** 2026-09-18
**Branch:** `release/afl-v1-freeze`
**Master base SHA:** `a1aaffa` (merge of PR #12, `feature/final-afl-ux-polish`, itself built on `55bae24` / PR #11, `fix/multi-builder-lineup-confirmation`)

This document is the freeze checkpoint for AFL v1: what exists, what was
validated, what's known to still be limited, and what happens next. From
this point, AFL development is expected to be primarily bug fixes while
active development moves to NBA/multi-sport expansion (see
[NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md)).

## Major implemented capabilities

- **Modelling**: margin-of-victory-adjusted Elo (team H2H), Poisson
  team-strength (line/total), Huber→NB2 regression (player disposals,
  promoted over an earlier Ridge model on real bias evidence), a hurdle
  model (player goals, chosen after confirming genuine zero-inflation in
  the data). XGBoost/LightGBM boosting evaluated and rejected on holdout
  (0.0 ensemble weight) — a recorded negative result, not a hidden one.
- **Leakage prevention**: point-in-time feature snapshotting, adversarial
  append-a-future-match tests, chronological (never k-fold) backtest
  splitting, bootstrap confidence intervals on every promotion decision,
  out-of-sample ablation.
- **Pricing**: on-demand fair-odds pricing at any threshold (not just
  bookmaker-posted lines) from persisted model state — no model is ever
  refit inside a pricing request.
- **Real market integration**: live odds from 9 Australian bookmakers via
  The Odds API, proportional de-vig, cross-book consensus, and a
  rule-based Market Monitor anomaly-detection layer (divergence,
  dispersion, curve-integrity, stale-quote checks).
- **Multi Builder**: correlation-aware multi-leg combination search (hard
  rejection of strongly-correlated pairs, warnings on moderate ones), plus
  an additive Same Game Pricing joint-probability panel backed by a
  validated (bootstrap-CI-confirmed) dependence coefficient — never a
  fabricated joint probability.
- **Prospective evaluation** (the project's core evidentiary claim): three
  independent freeze-before-outcome/settle-once/never-overwrite datasets,
  now all carrying real settled evidence — see Final test counts /
  Results below and the README's "Prospective evaluation" section for the
  full numbers and caveats.
- **Product frontend**: a React 19/TypeScript SPA across ~20 pages, dark
  graphite/teal/gold visual identity, information architecture organised
  around user intent (Home/Matches/Players/My Bets primary, Insights/
  Advanced grouped dropdowns) rather than internal module names. Every
  page that previously received only a global design-token restyle was
  brought up to the same standard during this freeze pass (PageHeader
  intros, progressive disclosure on the heaviest technical pages, grouped
  Prop Insights tabs, a new "visual history" chart on Player Research).
- **Player Research**: teammate and opponent context comparison — with/
  without-teammate and against-opponent-vs-others splits, evidence-
  sufficiency-ranked discovery (never effect-size-ranked), adjusted-vs-raw
  differences with confounders explicitly disclosed.
- **Market Movement Explorer**: genuine, unaligned bookmaker-quote and
  model-observation timelines per match/market, with an explicit
  never-fabricate-a-closing-line guarantee.
- **Model Registry / Model Evaluation**: current champion per market with
  promotion rationale headline-first, full run history and promotion audit
  trail behind progressive disclosure, live prospective evaluation
  composed in (not recomputed).
- **Trading Monitor / Pricing QA**: composes the existing Market Monitor
  detection engine unchanged and adds model-side value movement tracking
  (`ModelValueObservation`) as the one genuinely new signal.
- **B2B API productisation**: API-key-authenticated `/api/v1/pricing/*`
  and `/api/v1/market-intelligence/*` routes, per-consumer rate limiting,
  a shared error-response shape, request-ID traceability.
- **CI/CD**: GitHub Actions CI (backend tests, lint, migration checks, a
  real Postgres-integration matrix, frontend lint/tests/build, Docker
  build) running for real against the live repository — 52+ runs, current
  and green on `master`. A GHCR-published, digest-pinned backend image.
- **Live data pipeline**: a scheduled GitHub Actions workflow running the
  real live cycle every ~15 minutes against a real external Postgres —
  120+ real scheduled runs, which is what has, as of this write-up,
  exhausted the free-tier Odds API usage quota (a real operational fact,
  not a hypothetical failure mode).

## Final test counts

| Suite | Result |
|---|---|
| Backend (`pytest -q`, full suite) | **1,999 collected, 1,998 passed, 1 skipped** |
| Backend Postgres-integration subset (real Postgres 18, `docker compose`) | **335 passed** |
| Frontend lint (`oxlint`) | Clean (1 pre-existing, unrelated fast-refresh warning) |
| Frontend type-check (`tsc -b`) | Clean |
| Frontend tests (`vitest`) | **109 passed** across 6 files |
| Frontend production build (`vite build`) | Clean (one pre-existing chunk-size advisory, not an error) |

No release-blocking bug was found during this validation pass. One real,
non-blocking bug was found and fixed during the run-up to this freeze (see
below) and is already merged into `master`.

## Bug fixed during this freeze cycle

Multi Builder's "confirmed players only" filter could silently drop an
entire still-unconfirmed team's legs from a tier while the match's overall
readiness still read "ready" (readiness only required one confirmed player
*anywhere* in the match, not both teams) — every resulting empty tier
showed the same generic "doesn't meet the probability gates" message used
when the legs never existed at all, indistinguishable from a genuine
shortfall. Fixed (`fix/multi-builder-lineup-confirmation`, merged as PR
#11): a specific message now distinguishes "not enough CONFIRMED legs" from
"not enough legs at all," threaded through to the Multis hub page. Full
per-team confirmation is not yet required for a "ready" state — tracked as
a roadmap item, not resolved by this fix (see README Roadmap).

## Known limitations

Full detail and exact current numbers live in the README's "Limitations"
section — summarised here for the freeze record:

- Manual AFL team-selection entry (no automated lineup feed exists).
- The Odds API free-tier usage quota is currently exhausted — no fresh
  bookmaker prices are being ingested for upcoming matches until the
  monthly reset or a plan upgrade; historical data and model pages are
  unaffected.
- The live-cycle **data pipeline** is deployed and running for real
  (external Postgres, scheduled GitHub Actions); the product **web app**
  (Render backend + frontend) is not deployed anywhere — running it means
  running it locally.
- Prospective evidence is real but still early: 621 unique player-matches
  (real bookmaker capture) and 323 unique events (pricing-engine snapshots,
  including a first-but-tiny 7-unique-event team-market slice) and 11
  settled Same Game Multi combos (still exploratory). None of this
  supports a profitability claim — see README for the ROI/calibration
  distinction kept explicit throughout.
- SGM bookmaker-price comparison is "no data" everywhere — no odds
  provider integration exposes a genuine bookmaker SGM/parlay price.
- Frontend test coverage (109 tests) is real but proportionally lighter
  than backend coverage (1,999 tests).
- Single sport, single fixture provider, single odds provider, single
  region (AU).
- The Multi Builder both-teams-confirmed gap noted above.

## Production / deployment status

Precisely, to avoid the ambiguity a vaguer statement would create:

- **Live and real:** GitHub Actions CI (every PR/push), the GHCR image
  publish, and the scheduled live-cycle data pipeline against a real
  external Postgres with real secrets — this is genuine production data
  collection, not a demo.
- **Not deployed:** the Render web service (backend API) and Render
  static site (frontend). `render.yaml` exists and is CI-verified (the
  image it references builds and boots) but has never been applied to a
  live Render account. There is no public URL serving this product to end
  users today.
- **No production infrastructure was created, modified, or deployed
  during this freeze pass.** All validation (including the Postgres-
  integration test subset) ran against a local Docker Postgres container,
  torn down after use — never against the real external Postgres the live
  cycle writes to.

## Statement on future AFL development

AFL v1 is considered feature-complete for this development phase. From this
point forward, changes to the AFL surface of this codebase should primarily
be **bug fixes and data/operational maintenance** (e.g. renewing the odds
API plan, letting prospective datasets accumulate, addressing the
Multi Builder confirmation gap) rather than new AFL features, while active
development attention moves to NBA/multi-sport expansion per
[NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md). This is a statement of
intended focus, not a technical lock — nothing in the codebase prevents
further AFL feature work if a genuine need arises.
