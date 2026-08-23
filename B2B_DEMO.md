# AFL Pricing & Market Intelligence Engine — B2B Overview

Audience: a sportsbook, sports-data company, or betting-tech product team
evaluating this engine as a data/pricing feed or acquisition target.

## What problem this solves

Sportsbooks and data platforms need a **model-generated fair price** for AFL
markets that is independent of, and can be compared against, their own
trading lines — for pricing new/thin markets, sanity-checking trader lines,
or as a third-party signal. This engine produces that: probabilities and fair
odds for team and player markets, computed from historical performance data
and statistical models, with **full provenance** (which model, trained when,
on what data cutoff) attached to every number. It is not a betting product —
there is no staking, no bet placement, and no "recommended bet" concept
anywhere in the API surface.

## Supported AFL markets

| Market family | Markets | Arbitrary thresholds/lines? |
|---|---|---|
| Team | H2H (home/draw/away), line (handicap), total points | Yes — any line, any time |
| Player disposals | Over/under N.5 disposals | Yes — any threshold, not just preset lines |
| Player goals | N+ goals (anytime scorer through multi-goal) | Yes — any threshold |

Every market can be priced for **any upcoming AFL match** the system has
fixture data for, not just matches a bookmaker currently quotes — the engine
can produce a fair price for a line nobody has posted yet.

## Modelling & evaluation methodology

- **Team markets**: Elo (H2H) and a Poisson team-strength model (line/total),
  fit via walk-forward validation — never re-fit inside a pricing request.
- **Player disposals**: a robust (Huber) regression over rolling-form
  features, priced through a Negative-Binomial count distribution calibrated
  on holdout residual dispersion.
- **Player goals**: a hurdle model (separate "scores at all" classifier +
  zero-truncated count) chosen because it matches the genuine zero-inflation
  in real scoring data, not a plain count model forced to fit it.
- **Historical backtesting**: all models are validated on a strict
  chronological holdout (tune on earlier seasons, evaluate on 2019 onward)
  against a naive baseline, with Brier score, log-loss, and calibration
  (ECE) reported per market and threshold bucket.
- **Model registry**: every promoted model, its predecessor, and the
  evidence behind each promotion is kept as a permanent, append-only audit
  trail (`/api/v1/model-registry`) — nothing is silently swapped.

## Why prospective snapshots matter

A backtest can look good and still be an artifact of hindsight — feature
leakage, retrospective threshold selection, or simply re-testing on the same
holdout until something clears the bar. This engine also maintains a
**prospective evaluation dataset** (`PricingSnapshot`): every price it
generates for a still-future match is frozen at generation time — model
probability, fair odds, the market context available then, lineup status —
and is never edited, overwritten, or regenerated after the fact, even when a
newer model version is promoted later. Only this frozen, forward-looking
record can honestly answer "how does this engine perform on matches it
hadn't seen yet," and it is kept structurally separate from the historical
backtest so the two are never conflated in any report this engine produces.

## Current limitations

- **No live evidence yet.** The prospective snapshot mechanism is wired into
  the live cycle and accumulating data every round, but has not yet
  accumulated enough settled outcomes to report a prospective Brier/log-loss
  figure. **This engine makes no claim, implicit or explicit, of beating
  bookmaker pricing** — that question is only answerable once the
  prospective dataset has settled volume, and will be reported honestly
  (including a negative result) when it does.
- **No authentication or rate limiting yet** — see
  [`docs/API_USAGE.md`](backend/docs/API_USAGE.md) for the intended approach.
- **SGM/correlated-leg pricing is research-only, not deployed.** Investigated
  correlation structure (player-vs-own-team-outcome is the one relationship
  found to matter; player-vs-teammate and player-vs-opponent pairings were
  close to independent) but no joint-probability model has been built or
  shipped from that research yet.
- **Single-sport, single data source.** Currently AFL only, on one
  fixture/results/odds provider.
- Dev/single-machine deployment — the performance figures in
  [`docs/API_USAGE.md`](backend/docs/API_USAGE.md) are in-process compute
  times, not production round-trip SLAs.

## Example integration flow

A typical read-only integration for a partner wanting a comparison feed:

1. **Poll health**: `GET /api/v1/integration-health` on a schedule; alert if
   `status == "degraded"` or a stale-data warning appears.
2. **Pull the current round**: `GET /api/v1/pricing/afl/current-round` once
   per refresh cycle — every match, team price, and player price for the
   round in one call (cached ~30s server-side).
3. **Compare against your own line** (optional): for any selection you
   already quote, call the matching `/api/v1/market-intelligence/*` endpoint
   to see this engine's probability alongside the live consensus and your
   best-price context — or skip market-intelligence entirely and just use
   the pricing feed as an independent signal.
4. **Query an arbitrary threshold on demand**: e.g. a trader wants a specific
   line not in the preset set — `GET
   /api/v1/pricing/afl/players/{id}/disposals?match_id=&threshold=27.5`
   returns a fair price for that exact line without waiting for a batch
   refresh.
5. **Track model provenance**: persist the `model_version` string alongside
   any price you store — `/api/v1/model-registry` lets you look up exactly
   what that version was, when it was promoted, and what evidence supported
   the promotion, at any point later.

See [`docs/API_USAGE.md`](backend/docs/API_USAGE.md) for endpoint-level
detail (auth, errors, response times) and
[`backend/docs/PRICING_ENGINE.md`](backend/docs/PRICING_ENGINE.md) for full
modelling and evaluation detail. Real example responses for every endpoint
above are in [`backend/docs/api_examples/`](backend/docs/api_examples/).
