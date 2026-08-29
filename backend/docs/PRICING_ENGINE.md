# AFL Pricing & Market Intelligence Engine — Technical Overview

Audience: a technical product/data team evaluating this as a B2B data/pricing
feed. Not written for bettors, and contains no staking or "bet this" language
anywhere in the API surface.

## 1. What markets the engine prices

| Market family | Markets | Arbitrary query? |
|---|---|---|
| Team | H2H (home/draw/away), line (handicap), total points | Yes — any handicap/total line, any time |
| Player disposals | Over/under N.5 disposals | Yes — any threshold |
| Player goals | N+ goals (0.5, 1.5, 2.5, 3.5+, ...) | Yes — any threshold |

Every priced market returns a `model_fair_odds = 1 / probability`, computed
independently of whether any bookmaker currently offers that market — the
engine can price a line or threshold nobody has quoted yet.

## 2. API surface (`/api/v1/`)

**Pricing** (`/api/v1/pricing/*`) — pure model belief, no bookmaker input:
- `GET /pricing/health`, `GET /pricing/model-health`
- `GET /pricing/afl/matches/{match_id}` — full team + player pricing for one match
- `GET /pricing/afl/current-round` — every match in the current upcoming round, one call
- `GET /pricing/afl/players/{player_id}/disposals?match_id=&threshold=22.5` — arbitrary threshold
- `GET /pricing/afl/players/{player_id}/goals?match_id=&threshold=1.5`

**Market Intelligence** (`/api/v1/market-intelligence/*`) — comparison against
real bookmaker markets, structurally separate from pricing (a pricing call
never reads a bookmaker price; this layer only reads a pricing result and
compares it against `OddsQuote`/`PlayerPropMarket`):
- `GET /market-intelligence/afl/matches/{match_id}/team/{market_type}?selection=&line_value=`
- `GET /market-intelligence/afl/players/{player_id}/{market_type}?match_id=&threshold=`

Returns: best sportsbook price, consensus no-vig probability (same-book devig
where possible, methodology always disclosed), market spread, outlier-book
detection, and `difference_pp` (model − consensus). Never feeds back into
pricing.

Every response carries `model_name`, `model_version`, `generated_at`,
`data_cutoff`, `confidence_tier`, and (player markets) `lineup_status` +
`is_stale`/`stale_reasons`.

### Example

```
GET /api/v1/pricing/afl/players/333/disposals?match_id=2070&threshold=22.5
{
  "player_name": "...", "expected": 17.9, "distribution_method": "nb",
  "distribution_params": {"mu": 17.9, "alpha": 1.42},
  "thresholds": [
    {"threshold": 22.5, "line_type": "over_under", "probability": 0.132, "fair_odds": 7.58}
  ],
  "confidence_tier": "higher_confidence", "is_stale": false,
  "calibration": {"evaluated_threshold": 20.0, "ece": 0.009, "n": 64282}
}
```

## 3. Data dependencies

- Team pricing: Elo + Poisson team-strength models, fit via walk-forward over
  `matches`/`team_match_stats` (no re-fit in the request path — see §6).
- Player pricing: reads already-persisted `PlayerDisposalProjection` /
  `PlayerGoalProjection` rows (refreshed by the live cycle whenever lineup or
  results data changes) — never re-fits the ~100k-row regression per request.
- Freshness/staleness: compares each row's frozen generation-time state
  against the currently-promoted model version and current lineup status.

## 4. Model architecture (all pre-existing, reused unchanged)

- **Team H2H**: Elo (primary — the better-validated model on holdout Brier
  score), rescaled to leave room for Poisson's draw probability (Elo has no
  draw concept). **Line/total**: Poisson team-strength state, `home_pmf`/
  `away_pmf` scored at any requested line via `prob_margin_over`/
  `prob_total_over`.
- **Disposals**: Huber regression (rolling 3/5/10/season/career averages +
  EWMA) → mean, priced via a Negative-Binomial (NB2) count distribution fit
  on holdout residual dispersion.
- **Goals**: a hurdle model — a separate `P(scores ≥ 1)` classifier, and a
  zero-truncated NB for the scored-at-least-once count — chosen over a plain
  NB because it matches genuine zero-inflation in the data (see §7 findings).

## 5. Evaluation methodology

- **Historical backtests** (2019–2025 holdout): disposal/goal models compared
  against a naive baseline (career/rolling average), with Brier/log-loss/ECE
  by threshold, already computed and persisted (`*_validation_metrics`
  tables) — this is what `calibration` in every response cites.
- **Prospective evaluation dataset** (new, this stage — `PricingSnapshot`):
  every price generated for a still-future match is frozen — model
  probability, fair odds, market context at that moment, lineup status — and
  never overwritten by a later model version or re-derived after the fact.
  Settlement reuses the exact primitives already used to settle prop
  observations/placed bets. **This is the only dataset that can honestly
  answer "does this engine beat the market prospectively"** — see §8.

## 6. Performance

Measured on this dev machine (SQLite, single uvicorn worker, `--reload` on):

| Operation | In-process compute | Notes |
|---|---|---|
| Single match pricing | ~550ms (cold) | Elo/Poisson walk-forward + ~90 threshold evaluations + calibration lookups |
| Round-wide pricing (1 match, current data) | ~550ms (cold), **<1ms (cache hit)** | 30s TTL cache around the whole response |
| Arbitrary-threshold player query | a few ms | reads one persisted row, evaluates its distribution at the requested threshold |

No model is ever fit/re-fit inside a pricing request. Observed HTTP
round-trip in this dev environment was ~2–2.7s regardless of cache state —
isolated by direct in-process timing to **not** be the pricing computation
(5ms for dataclass→Pydantic→JSON); it is dev-server overhead (`--reload`,
single worker, no connection pooling) and should be re-measured against a
production ASGI deployment before being treated as a real latency figure.

## 7. Elite-disposal-bias research (item 9 — read-only, no model changed)

Historical buckets confirm the known pattern: high (22–28 avg) and elite
(28+ avg) players are under-predicted by ~1.0 disposal on average; low-volume
players are over-predicted by ~0.5. A controlled OLS of `(predicted − actual)`
on player historical average, games-of-history, season, and TOG
(n=62,173, 1,191 players) found the historical-average coefficient remains
**negative and highly significant** (−0.146, p≈0) after controls — i.e. the
under-prediction is not explained away by sample size, season, or time-on-
ground. **Conclusion: this does not look like a pure small-sample
regression-to-mean artifact** (games-of-history's own coefficient runs the
opposite direction that story would need). The pattern — systematic,
graded, correlated with true ability level — is consistent with **Ridge
L2 shrinkage** (the promoted model uses `alpha=5.0`; shrinkage mechanically
pulls extreme predictions toward the population mean). This is a plausible,
testable hypothesis, not a confirmed cause — the next step (not taken here,
per this stage's explicit boundary) would be comparing bias against a
lower-alpha/unregularized baseline on genuinely held-out data before
touching the promoted model.

## 8. SGM correlation research (items 10–12 — research only, nothing deployed)

Spearman correlations on the full historical `PlayerMatchStat`/`Match`
record (101,521 player-match rows, 2,250 matches):

| Pair | ρ | n | Read |
|---|---|---|---|
| Player disposals ↔ own team margin | +0.100 | 101,521 | weak positive |
| Player goals ↔ own team score | +0.164 | 101,521 | weak-moderate positive |
| Player goals ↔ match total | +0.099 | 101,521 | weak positive |
| Player disposals ↔ teammate disposals | +0.016 | 1.09M pairs (4,500 team-matches) | ~negligible |
| Player disposals ↔ opposing player disposals | +0.013 | 50,000 (subsampled) | ~negligible |
| One player scoring 2+ ↔ # teammates also scoring | −0.027 | 101,521 | ~negligible, not positive |

**Pseudo-replication note (item 7)**: the paired rows above are clustered by
match/team, not independent — reported as descriptive evidence with unique
match counts stated, not as an independence-adjusted hypothesis test; a
proper SGM backtest would need clustered/bootstrap CIs by player-match.

**Reading**: same-team and same-match player-vs-player pairings
(disposals↔teammate, disposals↔opponent, multi-forward scoring) are
essentially uncorrelated in this data — independence is a defensible
approximation for *those specific* leg pairings. The real, non-trivial
correlation is between a **player's output and their own team's match
outcome** (margin/score) — exactly the category `multi_builder.py` already
hard-rejects when combining a team-directional leg with a same-team player
leg. No evidence here justifies building a general joint-probability model
yet.

**Recommended approach if/when justified**: an empirical-conditional method
(bucket by team-outcome bin, read the empirical joint frequency) over a
copula or full multivariate simulation — it's the simplest model that can
capture the one correlation actually found to matter (team-outcome-linked),
requires no new distributional assumptions, and is auditable against the
data on a par with the rest of this codebase's existing "evidence over
sophistication" pattern. A copula/Monte Carlo approach would be
over-engineering relative to what the data currently supports.

## 9. Known limitations

- No live market-linked outcome data yet exists to compute a genuine
  model-vs-market Brier/log-loss benchmark (§5's `PricingSnapshot` table is
  brand new this stage — it has to accumulate settled observations first).
- Opponent-strength was not included as a confound in the elite-bias study
  (time-boxed this stage) — noted explicitly, not silently omitted.
- Team pricing confidence is a single validated/not-validated flag sourced
  from the existing Elo holdout metric, not a graded tier.
- No authentication/billing (out of scope this stage, per explicit
  instruction).

## 10. Current live evidence status

**None yet.** The prospective evaluation dataset exists and is wired into
the live cycle (freezes every cycle, idempotent per model version), but has
zero settled rows as of this write-up. No claim of market superiority,
edge, or profitability is made or implied anywhere in this codebase's
pricing output — every probability is presented as a model estimate, and
`market_intelligence` responses disclose methodology and sample size
alongside every comparison.
