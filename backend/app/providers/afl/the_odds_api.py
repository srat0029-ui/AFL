"""AFL's view of The Odds API: which of the provider's AFL market keys exist,
and which of them this project actually requests. The client itself is
sport-agnostic and lives in app/providers/the_odds_api.py - its names are
re-exported here because the AFL codebase imports the client from this
module.

Documented AFL player-prop market keys (see KNOWN_AFL_PLAYER_PROP_MARKET_KEYS
below) go well beyond disposals/goals (marks, tackles, clearances, kicks,
handballs, AFL Fantasy points, first/last goalscorer) - this project only
MODELS disposals and goals today (see app/player_modelling/market.py), so
only requests the subset of markets it can actually turn into a
projection comparison (see MODELLED_MARKET_KEYS). The rest are listed
here for the market-availability audit (Section 24) to report honestly on
what the provider offers that this app doesn't yet use, without ever
silently fetching (and paying for) markets nothing consumes.
"""

from app.providers.the_odds_api import (  # noqa: F401
    AFL_SPORT_KEY,
    BASE_URL,
    DEFAULT_REGION,
    PROVIDER_NAME,
    STANDARD_MARKET_KEYS,
    StandardMatchOddsResult,
    TheOddsApiError,
    TheOddsApiProvider,
)

# Every AFL player-prop market key documented at the-odds-api.com as of this
# stage's research (see module docstring) - used for the market-availability
# audit, NOT all requested on every refresh (see MODELLED_MARKET_KEYS).
KNOWN_AFL_PLAYER_PROP_MARKET_KEYS: list[str] = [
    "player_disposals",
    "player_disposals_over",
    "player_goal_scorer_first",
    "player_goal_scorer_last",
    "player_goal_scorer_anytime",
    "player_goals_scored_over",
    "player_marks_over",
    "player_marks_most",
    "player_tackles_over",
    "player_tackles_most",
    "player_afl_fantasy_points",
    "player_afl_fantasy_points_over",
    "player_afl_fantasy_points_most",
    "player_clearances_over",
    "player_kicks_over",
    "player_handballs_over",
]

# The subset this project can actually turn into a model-vs-market
# comparison today - only disposals and goals have a promoted projection
# model (see app/player_modelling/market.py's PlayerMarket enum and its
# note that TACKLES/MARKS have no projection model behind them yet). Only
# these are ever requested from the API, so refresh-prop-odds never spends
# quota on a market this app can't do anything with.
MODELLED_MARKET_KEYS: list[str] = [
    "player_disposals",
    "player_disposals_over",
    "player_goal_scorer_anytime",
    "player_goals_scored_over",
]
