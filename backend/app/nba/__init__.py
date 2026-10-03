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

# Modelling defaults decided before any model exists, so they are applied
# consistently rather than rediscovered:
#
# - Every season from 2015-16 is stored, but 2015-16 to 2017-18 have no
#   player box scores for two whole teams (see docs/NBA_DATA_SOURCES.md).
#   Player models train from 2018-19 by default. Other training windows and
#   recency weighting are to be compared prospectively, not assumed.
# - Schedule features (previous game, rest days, back-to-backs) come from
#   nba_games, which is complete - never from the presence of player rows.
# - A game with no stored box score is an UNAVAILABLE outcome for its
#   players. It is never a zero-stat game.
DEFAULT_FIRST_TRAINING_SEASON = 2018  # the 2018-19 season
