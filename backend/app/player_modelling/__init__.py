"""Architecture for future player-level prop projections (disposals, goals,
tackles, marks, and eventually fantasy points) — deliberately built and
committed BEFORE any actual predictive model, so the shape doesn't get bent
around whichever market happens to get modelled first.

Nothing in this package computes a real projection yet. It exists to:

1. Give each future market its own PlayerFeatureBuilder (features.py) —
   disposals and goals should NOT share one model or one feature set (an
   explicit project requirement); this package's types make that the
   natural shape rather than something bolted on later.
2. Require every future model to output a full probability distribution
   (projection.py's ProjectionDistribution), not just a point estimate —
   "29.8 expected disposals" alone can't answer "P(30+)"; the interface
   makes that failure mode structurally unavailable.
3. Represent prop markets and their common lines (market.py) as typed
   values now, so pricing/comparison code written later has something
   concrete to import instead of inventing ad-hoc strings per call site.

See app/modelling/ for the equivalent, ALREADY-implemented pattern at team
level (Elo, Poisson, logistic, boosting) — this package is deliberately
architected the same way, one stage ahead of having real models in it.
"""
