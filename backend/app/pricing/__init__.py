"""B2B AFL Pricing & Market Intelligence Engine.

Two clean layers (see module docstrings below for each):
- Pricing: what our models believe, independent of any bookmaker.
- Market Intelligence: comparison of that pricing against real bookmaker
  markets when one exists.

Every model here already exists elsewhere in this codebase (Elo/Poisson for
team markets, the promoted disposal/goal regression + distribution models
for player markets) — nothing in this package re-implements or re-trains a
model; it only re-exposes already-validated model output through a stable,
versioned, commercially consumable surface. See app/pricing/team_pricing.py
and app/pricing/player_pricing.py.
"""
