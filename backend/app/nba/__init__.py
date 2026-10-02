"""NBA domain package — everything that encodes NBA's own rules. ORM models
live in app/models/nba/, the HTTP surface in app/api/routes/nba.py.

Scope, deliberately narrow: player-prop SINGLES for points, rebounds and
assists, evaluated prospectively against the closing market. No same-game
multis, no team markets, and — as of this foundation — no predictive model
and no data ingestion yet. Nothing in this package fabricates data.

Module map:

- markets.py     — the three prop markets, their box-score stat, and the
                   odds provider's market keys for them.
- projection.py  — the contract a future model must satisfy
                   (expected minutes x per-minute rate, as a distribution).
- asof.py        — THE information boundary. The only place history is read
                   for prediction purposes; every query takes a cutoff.
- prospective.py — freeze a prediction before tip-off, capture the closing
                   line once, settle once.
- status.py      — what data exists right now (read-only, for the API).

Nothing here imports from app/player_modelling, app/modelling or
app/pricing (AFL). Shared maths comes from app/core and app/edges.
"""

SPORT_CODE = "NBA"
