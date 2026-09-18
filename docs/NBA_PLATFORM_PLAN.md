# NBA Platform Plan

Status: **planning document only — no NBA code exists yet.** Written at the
AFL v1 freeze point (see [AFL_V1_RELEASE.md](AFL_V1_RELEASE.md)) so NBA work
starts from a deliberate plan rather than an ad hoc first PR. There is no
`docs/MULTI_SPORT_READINESS.md` in this repository to build on — this
document was authored directly from a fresh audit of the current codebase's
AFL-specific coupling, referenced below by real file/module path.

**Guiding principle, stated once so every section below can assume it:**
extract a shared abstraction *when NBA proves it's actually shared*, not
before. The current codebase already has real, working sport-agnostic seams
in a few places (see section B) — those get reused as-is. Everywhere else,
the honest move is to write NBA-specific code next to the AFL-specific code
it resembles, and only lift out a shared version once both sports' real
implementations exist and visibly duplicate each other. This document is
explicitly **not** a proposal to rewrite the AFL application into generic
abstractions first, and explicitly **not** a proposal to duplicate the whole
app into an `/NBA` copy. The target end state is one product, one backend,
one frontend build, with sport-specific modelling behind shared platform
capabilities — reached incrementally, never as a single big-bang rewrite.

---

## A. What remains AFL-specific

Code that encodes AFL's own rules and will never run for NBA without being
rewritten, not just parameterised:

- **Team modelling** (`app/modelling/elo.py`, `app/modelling/poisson_model.py`) —
  Elo with a margin-of-victory adjustment tuned to AFL's typical scoring
  margins; a Poisson model over goals/behinds as separate count processes.
  NBA's own team model is a different problem (much higher scoring, no
  behinds-equivalent, a completely different home-court/rest/travel feature
  set) — this is new modelling work, not a parameter change.
- **Player modelling** (`app/player_modelling/disposal_backtest.py`,
  `goal_backtest.py`, the Huber/hurdle model classes themselves) — disposals
  and goals are AFL stat categories. NBA's player props (points, rebounds,
  assists, threes) need their own distributional choices (points is closer
  to a NB2/normal-ish continuous count than AFL disposals; a hurdle model
  for "at least one made three" might reuse the *shape* but not the fitted
  model).
- **Ingestion providers** (`app/providers/afl/squiggle.py`,
  `afltables.py`, `afltables_players.py`, `round_labels.py`) — Squiggle and
  AFL Tables are AFL-only data sources with AFL-only quirks (finals round
  codes like "EF"/"GF", the round-label reconciliation routine). NBA needs
  its own fixture/box-score source(s) entirely.
- **Market taxonomy specifics** — AFL's `market_type` vocabulary
  (`h2h`/`line`/`total`, `player_disposals`/`player_goals`, `multi_plus`
  line type for goals) is AFL's own. NBA has a materially different and
  larger prop taxonomy (see section I).
- **Frontend copy, market labels, and stat names** — "Disposals", "Round
  28", team colours/short-names, the AFL nav copy ("Finals / Multis",
  "Player Props") are AFL-specific text living inside otherwise generic
  components.
- **SGM dependence coefficients** (`SgmDependenceCoefficient`, the
  `sgm_joint_model_backtest.py` study) — fit on AFL's own correlation
  structure between a player's output and their team's result. NBA's
  correlation structure (e.g. a star's points vs. blowout margin) is an
  unstudied, separate question.

## B. What becomes genuinely shared platform infrastructure

Code that is *already* sport-agnostic today, verified by reading it rather
than assumed — this is smaller than "everything not in section A," which is
the point: don't manufacture more shared surface than what's proven.

- **The `Sport` model and `sport_id` foreign key** (`app/models/sport.py`) —
  already exists, already threaded through ~10 tables (Season, Round, Team,
  Match, OddsQuote, PlayerPropMarket, ...). Adding NBA as a sport is
  inserting a second `Sport(code="NBA")` row, not a schema migration to
  *add* multi-sport support — that support is already there.
- **Provider DTO layer** (`app/providers/types.py`) — `Fixture`,
  `TeamStatLine`, `PlayerStatLine`, `OddsQuote`, `TeamOddsQuote`,
  `PlayerPropQuote` are already declared sport-agnostic on purpose (the
  module's own docstring: "a second sport is a new provider implementation,
  not a change to these shapes"). `sport_code` is a plain string field
  throughout, not a hardcoded AFL enum. This is the single biggest piece of
  "already shared" — reuse these dataclasses unchanged for every NBA
  provider.
- **Ingestion idempotency patterns** — upsert-by-external-id, point-in-time
  feature snapshotting, the "mark updated only if a field actually
  changed" discipline. The *pattern* generalises even though each concrete
  ingestion module (AFL's `app/ingestion/*`) does not; NBA's ingestion
  modules should copy the pattern, not import AFL's module.
- **De-vig / consensus math** (`edges/overround.py`,
  `consensus_and_outliers.py`) — proportional de-vig and cross-book
  consensus are pure probability math with no sport-specific assumption
  baked in. Reusable as-is once NBA odds quotes exist in `OddsQuote` shape.
- **Freeze/settle/evaluate mechanism** (`PricingSnapshot`,
  `PropMarketObservation`, the uniqueness-constraint-as-no-op pattern, the
  Brier/log-loss/ECE evaluation math) — the *mechanism* (freeze before
  outcome, settle exactly once, never overwrite) doesn't know what sport
  produced the frozen price. What it freezes (a `(match, market, selection,
  model_version)` tuple) is already generic; `market`/`selection` strings
  are already sport-agnostic containers.
- **Model Registry's promotion-gate shape** (`ModelPromotionEvent`, the
  Brier-win/log-loss-non-regression/ECE-threshold/bootstrap-CI promotion
  criteria) — the *criteria* are proper scoring rules, not AFL rules. An
  NBA model registry entry uses the exact same gate logic against NBA's own
  backtest numbers.
- **API platform** (`require_api_key`, `ApiUsageRecord`, rate limiting,
  the shared error-response shape) — entirely sport-agnostic already; NBA
  pricing routes sit behind the exact same dependency.
- **Operational monitoring shape** (`LiveCycleRun`'s durable step
  lifecycle, the staleness-bucket pattern, `ModelValueObservation`'s
  append-only movement log) — the *shape* of "did the last cycle succeed,
  what's stale" doesn't care which sport the cycle ran for; a second
  sport's live cycle gets its own run rows in the same table, not a new
  table.
- **Frontend design system** (`components/ui/*` — `PageHeader`, `StatTile`,
  `Tabs`, `FilterChips`, `EmptyState`, `Skeleton`, the graphite/teal/gold
  CSS tokens in `index.css`) — already sport-agnostic presentational
  components with zero AFL-specific logic; see section M.

## C. Proposed repository/module structure

No `/NBA` copy of the application. Mirror the existing AFL-specific
directory shape one level down, next to the existing AFL code, so shared
modules stay genuinely shared and sport-specific modules stay obviously
separate without a second app to maintain:

```
backend/app/
  providers/
    types.py            # already shared (section B) — unchanged
    afl/                # existing, unchanged
    nba/                # NEW — squiggle-equivalent NBA fixture/box-score/odds providers
  modelling/            # AFL team models today; becomes a package boundary,
                         # not a rename — NBA team models live in nba_modelling/
                         # (new), not inside modelling/ itself, to avoid
                         # forcing a premature shared base class
  nba_modelling/         # NEW, mirrors app/player_modelling/'s shape for NBA's
                         # own team + player models
  pricing/              # extend only if a genuinely shared pricing helper
                         # emerges (e.g. a second sport also wants Monte
                         # Carlo SGM) — otherwise app/nba_pricing/ mirrors
                         # app/pricing/ instead of editing it in place
  market_monitor/        # reused as-is once NBA writes to the same tables
                         # (already sport-agnostic per section B)
  api/routes/
    nba_*.py             # NEW route modules, mirroring the afl_*/existing
                         # route module split — never edit an existing AFL
                         # route file to "add an if sport==nba branch"
frontend/src/
  pages/                 # NBA-specific pages added alongside AFL pages,
                         # reusing components/ui/* — no separate NBA app,
                         # no separate build
  components/ui/         # already shared (section B) — unchanged
```

The rule of thumb: **a new file next to the old one, not a new folder above
both.** If an NBA module ends up importing 80% of an AFL module unchanged,
*that's* the signal to extract a shared helper — discovered from two real
implementations, not designed up front from one.

## D. Sport identity/configuration

- Add one row: `Sport(code="NBA", name="National Basketball Association")` —
  no schema change, since `sport_id` FKs already exist project-wide.
- A small `SportConfig` (new, e.g. `app/config_sport.py` or a dict keyed by
  sport code) carrying only what genuinely differs per sport at runtime:
  season structure (AFL: single round-robin + finals; NBA: 82-game season +
  play-in + playoffs — different enough that "round_number" as AFL uses it
  may not map cleanly, see section E), display name, team-colour palette
  source, and which market taxonomy applies (section I). Resist the urge to
  make this config-driven for everything — most differences (model classes,
  provider parsing) are code differences, not config differences, and
  forcing them into a config dict just hides real branching behind a data
  structure.
- Frontend: a sport is selected by **route namespace**, not a global toggle
  that re-themes the whole app (see section L) — `SportConfig`'s frontend
  half is just "which nav items and routes exist for this sport."

## E. NBA fixture/result ingestion

- New `app/providers/nba/<source>.py` implementing the existing `Fixture`
  dataclass (section B) — reuse it unchanged; do not add NBA-specific
  fields to it until a real need appears (it already carries a generic
  `home_score_breakdown: dict[str, int]` for sport-specific scoring
  subcomponents, which covers e.g. per-quarter NBA scoring the same way it
  covers AFL's goals/behinds split).
- NBA's season structure (82 games, conferences/divisions, a play-in
  tournament, best-of-7 playoff series) does not map cleanly onto AFL's
  `Round` model (one round = one set of fixtures in a single round-robin).
  Two options, decided once real NBA fixture data is in hand rather than
  guessed here: (1) treat each NBA game day or week as a `Round` row
  (loosest fit, least new schema), or (2) add a nullable
  `series_game_number`/`playoff_round` pair to `Match` used only when
  `sport_id` is NBA. Prefer (1) first — it needs zero schema change — and
  only move to (2) if reporting genuinely breaks without series context.
- Reuse the existing idempotent-upsert-by-external-id ingestion pattern
  (section B) as the template for a new `app/ingestion/nba_fixtures.py` —
  copy the pattern, do not import AFL's `app/ingestion/fixtures.py` and
  branch inside it.

## F. NBA team modelling

- New `app/nba_modelling/` package (section C). Do not attempt to
  generalise Elo/Poisson into a "sport-agnostic team model base class"
  before an NBA team model exists — an Elo-style rating can very plausibly
  work for NBA too (rating systems are a genuinely cross-sport idea), but
  the margin-of-victory scaling function, the home-court adjustment
  magnitude, and back-to-back/rest-day fatigue features are all NBA-
  specific tuning that has to be derived from real NBA data, not carried
  over from AFL's fitted constants.
- Walk-forward chronological backtesting, bootstrap CIs, and the
  promotion-gate criteria (section B) are reused as-is — an NBA model run
  goes through `ModelPromotionEvent`/`ModelRun` exactly like an AFL one,
  just with `sport_id` set to NBA and its own `EVALUATION_START_YEAR`.

## G. NBA player-stat modelling

- New player-prop model classes for points/rebounds/assists/threes in
  `app/nba_modelling/` (or a `nba_player_modelling/` sibling if the module
  grows large enough to warrant its own package — decide once it exists,
  matching how `app/modelling/` and `app/player_modelling/` are already
  split for AFL by team-vs-player rather than by an upfront plan).
- Distributional choice per stat needs its own audit exactly like AFL's
  hurdle-model decision was justified by measuring real zero-inflation
  (README's Modelling section) — do not assume NB2 or a hurdle model
  transfers; measure NBA's own points/rebounds/assists distributions
  before choosing.
- Reuse the calibration/promotion-gate machinery (section B) unchanged.

## H. NBA bookmaker-market ingestion

- The Odds API (already integrated for AFL) also covers NBA — likely the
  single lowest-effort part of this whole plan, since `app/providers/afl/
  the_odds_api.py`'s *parsing* of `TeamOddsQuote`/`PlayerPropQuote`
  responses is already provider-shaped, not AFL-shaped; the AFL-specific
  part is the `sport_key` string ("aussierules_afl") and the market-key
  translation table (section I). A new `app/providers/nba/the_odds_api.py`
  (or a shared `the_odds_api.py` parameterised by `sport_key`, decided once
  written — see the "extract when duplication is real" principle) supplies
  `sport_key="basketball_nba"` and its own market-key mapping.
- De-vig/consensus (section B) reused unchanged once NBA `OddsQuote` rows
  exist.

## I. Market taxonomy differences

AFL's current `market_type` vocabulary is small and fixed
(`h2h`/`line`/`total` for team markets, `player_disposals`/`player_goals`
for player props, with `line_type` distinguishing `over_under` from
`multi_plus`). NBA's real prop taxonomy is both larger and structurally
different:

- Team markets: h2h (moneyline) and spread/total map directly onto AFL's
  existing shape. No new concept needed there.
- Player props: points, rebounds, assists, threes made, and combinations
  (points+rebounds+assists) are standard NBA bookmaker markets AFL has no
  equivalent for. `PlayerMarket` (currently an AFL-scoped enum in
  `app/player_modelling/market.py`) needs either a parallel NBA enum or
  (if genuinely shared handling emerges) a `sport_id`-scoped market
  registry — decided once NBA's real market set from a live odds pull is
  in hand, not designed against a guessed list.
- Combination props (points+rebounds+assists) have no AFL analogue at all
  and are a genuinely new modelling problem (a derived distribution over a
  sum of correlated stats), not a market-taxonomy extension.

## J. Prospective prediction freezing from day one

This is the one item where the AFL project's own history is a direct,
explicit lesson for NBA: AFL's prospective-evaluation infrastructure
(`PricingSnapshot`, `PropMarketObservation`) was built well after the
modelling and pricing layers existed, meaning real prospective evidence
only started accumulating from whenever that infrastructure shipped, not
from when the models did. **NBA should wire a `PricingSnapshot`-equivalent
freeze path into its very first live pricing run**, even before NBA pricing
is "finished" or promoted out of a challenger state — freezing a
challenger's price costs nothing and starts the evidence clock immediately,
whereas skipping it means re-living AFL's own "evidence is not there yet"
period unnecessarily. Concretely: NBA's first `run-live-cycle`-equivalent
invocation should freeze prices the same cycle it starts generating them,
using the existing `PricingSnapshot` table (`sport_id`-scoped, no schema
change) rather than deferring "prospective tracking" to a later phase.

## K. Settlement

- Reuse `prop_settlement.py`'s settlement primitives and the
  freeze-once/settle-once/never-overwrite discipline unchanged — settling
  an NBA prop is "was the actual stat over/under the frozen line," which is
  the same operation AFL disposals/goals settlement already performs
  generically over a `(market_type, threshold, actual_value)` shape.
- NBA-specific work here is narrow: mapping NBA's own box-score result
  format into the same `actual_value` shape settlement already expects —
  an ingestion-layer concern, not a new settlement mechanism.

## L. Frontend sport switching/navigation

- **Route-namespaced, not a global sport toggle that re-skins the app.**
  AFL's current IA (Home/Matches/Players/My Bets primary, Insights/
  Advanced dropdowns — see README) stays exactly as-is under its current
  routes. NBA gets its own top-level nav entry (e.g. a peer to "Matches" —
  exact placement is a product decision at NBA-build time, not this
  document's job to dictate) leading to NBA-scoped routes
  (`/nba/matches`, `/nba/players`, ...), reusing `App.tsx`'s existing
  `NavGroup`/`PRIMARY_LINKS` pattern rather than inventing a second
  navigation component.
- A visitor picks a sport by navigating to it, the same way they pick
  "Players" vs. "Matches" today — no cross-cutting "current sport" client
  state that every component needs to read, which is exactly the kind of
  premature shared abstraction this document's guiding principle argues
  against building before it's needed.
- AFL's existing routes, component tree, and API calls are **completely
  unaffected** — see section N.

## M. Shared design system

- `components/ui/*` (`PageHeader`, `StatTile`/`StatTileRow`, `Tabs`,
  `FilterChips`, `EmptyState`, `Skeleton`, `Logo`) and the graphite/teal/
  gold CSS tokens (`index.css`) are already sport-agnostic — confirmed by
  reading them, not assumed — and get reused by NBA pages unchanged from
  day one. This is the one area where "shared platform infrastructure
  from the start" is correct, precisely because it already exists and
  already has zero AFL-specific logic in it (unlike the modelling/pricing
  layers in section A, which would need to be *built* generic rather than
  *used* generic).
- The brand mark/wordmark ("AFL Research & Markets") is AFL-specific text
  in `Logo.tsx`/`App.tsx` — becomes a small per-product or per-sport-group
  configuration point when NBA is added, not a reason to touch the design
  tokens themselves.

## N. How existing AFL functionality remains unaffected

- No existing AFL table, column, or model class is modified to "make room"
  for NBA — `sport_id` scoping already exists, so NBA rows are simply rows
  with a different `sport_id` in the same tables where that's genuinely
  shared (section B), and entirely separate tables/modules where it's not
  (section A).
- No existing AFL route, page component, or API contract changes. New NBA
  routes are additive (`app/api/routes/nba_*.py`, `frontend/src/pages/`
  NBA pages) rather than existing AFL routes gaining an `if sport == ...`
  branch.
- The existing backend test suite (1,999 tests) and frontend suite (109
  tests) continue to pass unmodified by NBA work — NBA work adds its own
  new test files rather than editing AFL test fixtures to "also cover
  NBA," which would risk exactly the kind of coupling this plan is
  designed to avoid.
- The live-cycle scheduler's AFL run and a future NBA run are two separate
  scheduled invocations (or two steps in one workflow run, decided at
  build time) — never one invocation branching mid-run on sport, so an NBA
  ingestion bug cannot take down the AFL live cycle and vice versa.

## O. Incremental migration order

Deliberately ordered so every step ships something real and testable
before the next one starts, and so the project could stop after any step
with NBA in a coherent (if incomplete) state rather than a half-migrated
one:

1. **Sport identity** (section D) — add the `Sport(code="NBA")` row and
   `SportConfig` entry. Zero user-visible change; unblocks everything else.
2. **Fixture/result ingestion** (section E) — one NBA provider, real NBA
   matches landing in the existing `Match` table. Verifiable via the
   existing admin/API surface with no new frontend work.
3. **Bookmaker odds ingestion** (section H) — NBA `OddsQuote`/prop-quote
   rows flowing in, reusing de-vig/consensus (section B) unchanged.
   Verifiable independent of any NBA model existing yet.
4. **NBA team model** (section F) — Elo-or-equivalent for NBA, through the
   existing promotion-gate/backtest machinery. First point where "does the
   model beat naive" becomes answerable for NBA.
5. **NBA pricing + prospective freezing together** (sections F/J) — ship
   these in the same step, not pricing first and freezing later, per
   section J's explicit lesson from AFL's own history.
6. **NBA player-stat models** (section G) — points/rebounds/assists/threes,
   each going through the same measure-first distributional-choice
   discipline AFL's hurdle-model decision modelled.
7. **Settlement + real market tracking** (section K) — real evidence
   starts accumulating from step 5's freeze point; this step is "turn the
   crank," not new infrastructure.
8. **Frontend NBA surface** (sections L/M) — NBA nav entry, match/player
   pages, reusing the existing design system. Deliberately last: by this
   point there's real NBA data to build a genuine (not placeholder) UI
   against, matching how the AFL frontend was always built against real
   backend data rather than mocked ahead of it.
9. **Market Monitor / Trading Monitor coverage** (section B) — extend the
   existing composition layers to include NBA cases once NBA has enough
   live data flowing for anomaly detection to be meaningful, not before.
10. **Revisit extraction candidates** — only now, with two real sports'
    worth of code in hand, look back at sections A/B's boundary and decide
    whether anything assumed AFL-specific in section A turned out to
    duplicate cleanly enough with NBA's version to be worth lifting into a
    shared module. This step is explicitly allowed to conclude "not yet" —
    premature extraction is the failure mode this whole document is
    written to avoid.
