# AFL v1 Release — Freeze Checkpoint (refreshed)

**Refresh date:** 2026-09-23
**Branch:** `release/afl-v1-freeze`
**Original freeze base SHA:** `a1aaffa` (merge of PR #12, `feature/final-afl-ux-polish`)
**Master base used for this refresh:** `3777604e58a6cbeccfdcfc1dafa4c8afbc097f09` (merge of PR #15,
`feature/teammate-context-time-windows`, itself following PR #14's Multi Builder selection refinement)
**Release branch HEAD after this refresh:** `git rev-parse release/afl-v1-freeze` after this doc's own commit — reported exactly (full 40-character SHA) in the task's final report and visible on PR #13 once pushed.

This document is the freeze checkpoint for AFL v1: what exists, what was
validated, what's known to still be limited, and what happens next. The
original freeze (2026-09-18) predated two corrective features that had
already been designed and were merged to `master` afterward:

1. **Multi Builder selection refinement** (PR #14) — fewer-legs-first
   construction, tighter tier caps, an honest bookmaker-coverage proxy, and
   exact-threshold calibration in ranking.
2. **Teammate-context time windows and comparable-tenure filtering**
   (PR #15) — an explicit, never-silently-broadened time window for Player
   Research's with/without-teammate comparison, and exclusion of games
   outside a teammate's recorded club tenure from that comparison.

This refresh brings the release branch up to date with both, re-validates
the whole suite from scratch, and updates every figure in this document and
the README to the numbers produced by that re-validation — the counts below
are **not** carried over from the original freeze pass.

From this point, AFL feature development is frozen (see "Statement on
future AFL development" below); active development moves to NBA/multi-sport
expansion (see [NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md)).

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
  out-of-sample ablation. Teammate-context's recent-form trailing baseline
  (below) follows the same discipline: every game's baseline uses only
  strictly earlier games, whatever time window or tenure-eligibility
  exclusion is applied to the comparison itself.
- **Pricing**: on-demand fair-odds pricing at any threshold (not just
  bookmaker-posted lines) from persisted model state — no model is ever
  refit inside a pricing request.
- **Real market integration**: live odds from 9 Australian bookmakers via
  The Odds API, proportional de-vig, cross-book consensus, and a
  rule-based Market Monitor anomaly-detection layer (divergence,
  dispersion, curve-integrity, stale-quote checks). Only bookmakers marked
  eligible for this product (`Bookmaker.eligibility == included`) count
  toward any market-coverage signal or multi construction — excluded
  exchanges/informational-only providers are never counted as evidence a
  market is more or less widely offered.
- **Multi Builder** (refined this cycle, PR #14): correlation-aware
  multi-leg combination search (hard rejection of strongly-correlated
  pairs, warnings on moderate ones), plus an additive Same Game Pricing
  joint-probability panel backed by a validated (bootstrap-CI-confirmed)
  dependence coefficient — never a fabricated joint probability. Selection
  itself was corrected on audited evidence (see below): it now prefers the
  **fewest legs** that reach each tier's odds band (tier caps **3/4/5/6**,
  down from 5/6/7/8), and per player offers the **most demanding
  qualifying line** rather than defaulting to the easiest threshold.
  **"Main Markets Only" is on by default**: a leg is preferred when it's
  offered by at least half of the match's *eligible* bookmakers — an
  explicitly-labelled **coverage proxy**, never popularity or liquidity,
  and it can only break ties between otherwise-similar legs (a weakest leg
  more than 5 percentage points stronger always wins regardless of
  coverage). **Exact-threshold calibration**: a leg's calibration evidence
  only contributes to ranking when the promoted model's calibration was
  genuinely evaluated at that leg's own required stat threshold; a 15+ line
  that would otherwise borrow the nearest evaluated threshold's (e.g. 20+)
  calibration record gets **no ranking credit for it** and the UI marks
  that calibration as not-exact rather than showing it as if it were.
  **Post-settlement near-miss diagnostics** exist on Placed Bets ("4 of 5
  legs hit", "missed by 2 disposals") as review context, never a
  reclassification of a loss. See "Multi Builder audit result" below for
  the honest limitation on what this refinement does and does not prove.
- **Prospective evaluation** (the project's core evidentiary claim): three
  independent freeze-before-outcome/settle-once/never-overwrite datasets,
  now all carrying real settled evidence — see "Prospective evidence
  maturity" below and the README's "Prospective evaluation" section for
  the full numbers and caveats. **Unchanged by this refresh** — neither
  merged feature altered the live pipeline, and no new live-cycle runs
  were triggered as part of this work (see "Production / deployment
  status" below).
- **Product frontend**: a React 19/TypeScript SPA across ~20 pages, dark
  graphite/teal/gold visual identity, information architecture organised
  around user intent (Home/Matches/Players/My Bets primary, Insights/
  Advanced grouped dropdowns) rather than internal module names.
- **Player Research** (refined this cycle, PR #15): teammate and opponent
  context comparison — with/without-teammate and against-opponent-vs-
  others splits, evidence-sufficiency-ranked discovery (never
  effect-size-ranked), adjusted-vs-raw differences with confounders
  explicitly disclosed. The teammate comparison now runs over an
  **explicit, always-visible time window** (Current Season — the default —
  Last 2 Seasons, or Current Club Career), and only over games that fall
  inside the teammate's **comparable recorded tenure** at the club (see
  "Teammate-context: final behaviour" below). Discovery and the detail view
  always share the same window and the same eligibility classification —
  a candidate is never ranked under one window/eligibility rule and opened
  into a different one.
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
  real Postgres-integration matrix across PostgreSQL 16 and 18, frontend
  lint/tests/build, Docker build) running for real against the live
  repository. A GHCR-published, digest-pinned backend image.
- **Live data pipeline**: a scheduled GitHub Actions workflow running the
  real live cycle every ~15 minutes against a real external Postgres —
  120+ real scheduled runs as of the original freeze, which is what
  exhausted the free-tier Odds API usage quota (a real operational fact,
  not a hypothetical failure mode; status not re-verified this cycle — see
  the Odds API quota bullet under "Known limitations" below).

## Multi Builder audit result (honest statement, not a profitability claim)

The corrective pass that produced PR #14 was preceded by a reproducible,
read-only audit (`backend/scripts/multi_builder_audit.py`,
[docs/MULTI_BUILDER_AUDIT.md](MULTI_BUILDER_AUDIT.md)) of every placed
multi, every frozen Same Game Multi combination, and a legacy-vs-refined
replay over settled matches. The honest finding: **the old builder's
objective — maximise the weakest leg's own probability — tended to select
the easiest, shortest-priced lines, which then needed more legs to reach a
tier's target odds band, adding failure points and bookmaker margin.** The
refinement corrects this as a **construction/product-quality problem**: it
changes which legs and how many legs a multi is built from, using only
evidence already in the database (bookmaker coverage, exact-threshold
calibration, fewest-legs preference). It does **not** change or re-fit any
probability model, and it does **not** claim the refined builder wins more
often — the settled-multi sample available to compare against (tens of
placed/frozen multis, overlapping legs across options) is far too small to
support a hit-rate claim in either direction, and the audit document says
so explicitly rather than reporting a headline number without that
caveat.

## Teammate-context: final behaviour

- **Default window: Current Season.** Other windows: Last 2 Seasons,
  Current Club Career. The active window is always shown next to the
  headline result (e.g. "2026 season · 18 games together · 3 apart") and
  is **never silently widened** when a sample is too small — an
  insufficient current-season sample is reported honestly ("Only 2 games
  without this teammate this season. Current season comparison is
  insufficient.") with an explicit action to view a broader window, never
  a silent fallback.
- **Club scoping is unchanged and still safety-first:** the player's most
  recent club is still read from `PlayerMatchStat.team_id` (the source of
  truth for who played for whom on a given date), never from
  `players.current_team_id`.
- **Comparable-tenure filtering (new this cycle).** Every game inside the
  selected window is classified using only positive evidence from recorded
  match history:
  - **WITH** — the teammate has a same-club row in that exact match.
  - **ELIGIBLE WITHOUT** — the teammate has no row for the match, but their
    most recent recorded appearance at or before that point was for the
    *same* club (could be a genuine injury, rest, or omission — the kind
    of "without" evidence the comparison actually wants).
  - **EXCLUDED (outside comparable tenure)** — the teammate has no row for
    the match, and there is positive recorded evidence they had not yet
    joined the club, or their most recent recorded appearance was for
    *another* club. **Excluded games are never described as, or counted
    as, teammate-out evidence** — they are removed from every mean,
    median, milestone rate, sample size, confidence figure, adjusted
    effect, season breakdown, and discovery ranking, and shown only as
    visible audit context (an "Excluded from comparison" section, a
    per-season excluded count, a plain-English note stating how many games
    were excluded and why).
  - **Players who leave a club and later return** are handled purely from
    recorded club history: eligibility for a given game depends only on
    where the teammate's most recent appearance *up to that point* was, so
    a stint at another club in between is excluded, and eligibility
    resumes once the teammate's own recorded history shows them back at
    the club.
- **Illustrative example (Mitch McGovern / Wade Derksen at Carlton,
  Current Club Career window, real local data):**

  | | Together | Apart (naive) | Eligible apart | Excluded (outside tenure) |
  |---|---|---|---|---|
  | Old definition | 15 | 107 | — | *(not distinguished)* |
  | Refined definition | 15 | — | **7** | **100** |

  Derksen debuted for Carlton in 2026; the old split counted every one of
  the player's 107 Carlton games without a Derksen row as "apart", even
  though 100 of them were seasons before Derksen had ever played for the
  club. The refined split reports the same 15 "together" games, but only
  the 7 games genuinely inside Derksen's comparable tenure count as
  "apart" — the other 100 are visible as excluded audit context, not
  folded into the average. This is a **sample-honesty correction, not a
  causal claim** — nothing about *why* the player performed as they did in
  those 7 games is asserted.
- **Explicit limitation, stated in the product and here:** this codebase
  holds **no official club list-membership, injury, or selection-
  availability data**. "Eligible without" therefore means "no teammate
  match row, with recorded history consistent with the teammate plausibly
  being at this club" — it is **not** the same as "verified available but
  omitted", and the UI/API never claim otherwise.
- **Per-season breakdown:** where a window spans more than one season, a
  descriptive with/without table by season is shown (games and mean per
  side, per season, over comparison-eligible games only) so a reader can
  see whether a multi-season difference is consistent, driven by one
  season, or resting on very few eligible games — deliberately not
  compressed into an invented "consistency score".
- **Confidence** remains driven purely by comparison-eligible sample size
  (never by effect magnitude); a large apparent difference on a tiny
  eligible sample still reports low/insufficient confidence.
- **Baseline reuse across exclusion:** the recent-form trailing baseline
  behind the adjusted effect is computed from the player's whole prior
  club history (strictly earlier games only) — a game excluded from the
  actual teammate in/out comparison can still legitimately contribute to a
  *later* game's baseline, since it's genuine prior form even when it
  isn't comparable teammate-tenure evidence.

## Final test counts (this refresh — supersedes the original freeze numbers)

Re-run from a clean sync of `release/afl-v1-freeze` onto master `3777604`,
not copied from the prior freeze pass or from CI's last pre-refresh run
(cited for context as backend 2,065/1 skipped, frontend 130, Postgres 336 —
this refresh's own run reproduced those exact figures):

| Suite | Result |
|---|---|
| Backend (`pytest -q`, full suite, local `.env` isolated) | **2,066 collected, 2,065 passed, 1 skipped** |
| Backend Postgres-integration subset (real Postgres 18, throwaway `docker run`, exact CI file list — 24 files) | **336 passed** |
| Backend lint (`ruff check .`) | Clean |
| Frontend lint (`oxlint`) | Clean (1 pre-existing, unrelated fast-refresh warning — see Known non-blocking items) |
| Frontend type-check (`tsc -b`) | Clean |
| Frontend tests (`vitest`) | **130 passed** across 8 files |
| Frontend production build (`vite build`) | Clean (one pre-existing >500kB chunk-size advisory, not an error — see Known non-blocking items) |

No release-blocking issue was found during this validation pass. Merging
`origin/master` (carrying both feature branches) into `release/afl-v1-freeze`
produced **zero merge conflicts** — git's three-way merge resolved
README.md automatically (both sides had added independent, non-overlapping
paragraphs), and every other touched file was disjoint between the two
feature branches and the original freeze commit.

## Known non-blocking technical debt (recorded, not fixed this cycle)

Per this task's explicit scope, these are recorded but were **not**
addressed, since none is release-blocking:

- Vite production build reports one chunk >500kB after minification — a
  pre-existing code-splitting advisory, not a build error.
- One pre-existing `oxlint` fast-refresh warning in
  `WeeklyReviewOpportunityRow.tsx` (a file that exports both a component
  and helpers) — cosmetic, unrelated to either merged feature.
- The Multi Builder both-teams-confirmed gap noted below (pre-existing,
  unrelated to the selection refinement).

## Known limitations

Full detail and exact current numbers live in the README's "Limitations"
section — summarised here for the freeze record:

- Manual AFL team-selection entry (no automated lineup feed exists).
- The Odds API free-tier usage quota status is **not re-verified by this
  refresh** (no production action was taken — see below); as of the
  original freeze it was exhausted (`OUT_OF_USAGE_CREDITS`) and had not
  been confirmed reset. Treat it as still exhausted until an operator
  confirms otherwise against the real account.
- The live-cycle **data pipeline** is deployed and running for real
  (external Postgres, scheduled GitHub Actions); the product **web app**
  (Render backend + frontend) is not deployed anywhere — running it means
  running it locally.
- **Prospective evidence maturity: unchanged since the original freeze
  checkpoint (2026-09-18).** Neither merged feature touched the live
  ingestion/pricing/freeze pipeline, and no live-cycle run was triggered as
  part of this refresh, so the README's prospective-evaluation figures
  (621 unique settled player-matches, 2,920 settled `PricingSnapshot`
  predictions across 323 unique events including a first-but-tiny
  7-unique-event team-market slice, 11 settled Same Game Multi combos) are
  carried forward as last measured, not re-queried against the live
  database from this task. None of it supports a profitability claim — see
  README for the ROI/calibration distinction kept explicit throughout.
- **Multi Builder evidence limitation** (see "Multi Builder audit result"
  above): the settled-multi sample is far too small to claim an improved
  hit rate; the refinement is a construction/product-quality correction,
  not a demonstrated profitability improvement.
- **Teammate-context evidence limitation** (see "Teammate-context: final
  behaviour" above): no list-membership/injury/availability data exists in
  this codebase, so "eligible without" is a recorded-history proxy, not a
  verified availability signal; the comparison remains descriptive/
  associative, never causal.
- SGM bookmaker-price comparison is "no data" everywhere — no odds
  provider integration exposes a genuine bookmaker SGM/parlay price.
- Frontend test coverage (130 tests) is real but proportionally lighter
  than backend coverage (2,065 tests).
- Single sport, single fixture provider, single odds provider, single
  region (AU).
- The Multi Builder both-teams-confirmed gap (pre-existing, unrelated to
  the selection refinement — match readiness can read "ready" while only
  one team's lineup is confirmed).

## Production / deployment status

Precisely, to avoid the ambiguity a vaguer statement would create:

- **Live and real:** GitHub Actions CI (every PR/push), the GHCR image
  publish, and the scheduled live-cycle data pipeline against a real
  external Postgres with real secrets — this is genuine production data
  collection, not a demo. **Unaffected by this refresh.**
- **Not deployed:** the Render web service (backend API) and Render
  static site (frontend). `render.yaml` exists and is CI-verified (the
  image it references builds and boots) but has never been applied to a
  live Render account. There is no public URL serving this product to end
  users today.
- **No production infrastructure was created, modified, or deployed
  during this refresh.** No Render action, no production migration, no
  live-cycle trigger, no schedule change, no production secret change, no
  image promotion. All validation (including the Postgres-integration test
  subset) ran against a local, throwaway Docker Postgres 18 container,
  torn down immediately after use — never against the real external
  Postgres the live cycle writes to, and never against the shared local
  dev SQLite database either.

## Statement on future AFL development

**AFL v1 feature development is now frozen.** Both corrective features
identified as outstanding at the original freeze point (Multi Builder
selection quality, teammate-context time horizon) are merged and validated.
From this point forward, changes to the AFL surface of this codebase should
be limited to:

- **Genuine bug fixes** — a real defect found in existing behaviour, not a
  new capability.
- **Data / operational maintenance** — renewing the Odds API plan, letting
  the prospective datasets keep accumulating real settled evidence,
  routine ingestion upkeep.
- **Prospective evidence accumulation** — the live cycle continuing to run
  and settle is expected and desired; it requires no code change to
  produce more evidence over time.
- **Required compatibility work for shared multi-sport infrastructure** —
  if NBA work needs a genuinely shared module (Section B of
  [NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md)) adjusted in a
  backward-compatible way, that adjustment is in scope even though it
  touches code AFL also uses.

New major feature development moves to NBA/multi-sport per
[NBA_PLATFORM_PLAN.md](NBA_PLATFORM_PLAN.md). This is a statement of
intended focus, not a technical lock — nothing in the codebase prevents
further AFL feature work if a genuine need arises, but "a genuine need"
means a bug or a data/compatibility requirement, not a new feature idea.
