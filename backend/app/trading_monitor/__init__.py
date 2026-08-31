"""Trading Monitor / Pricing QA composition layer.

Answers "what changed, what looks unusual, what's stale, what needs
investigating" for a trading/pricing analyst. Deliberately a composition
layer, not a second detection engine: divergence, dispersion, outlier,
staleness-vs-context, and pricing-curve anomalies are all already detected,
scored, and persisted by `app.market_monitor` — this package reads that
system's own functions rather than re-implementing any of it. The one
genuinely new signal this package adds is model-side movement over time
(team win probability, player projections), because nothing else in this
codebase captures that history — see `app.player_modelling.
model_value_observations`/`model_movement`.
"""
