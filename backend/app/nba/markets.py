"""The NBA prop markets this project covers, and how each maps to a box-score
stat and to the odds provider's market keys.

Three markets only, by decision: points, rebounds, assists. Combination
props (points+rebounds+assists, ...) are a different modelling problem — a
distribution over a SUM of correlated stats — and are not represented here
until they are actually modelled.

Every NBA prop here is an over/under line, so unlike AFL there is no
`line_type` to carry around ("N+" ladders are just alternate over lines).
"""

from enum import Enum

from app.models.nba import NbaPlayerGameLog


class NbaPropMarket(str, Enum):
    POINTS = "player_points"
    REBOUNDS = "player_rebounds"
    ASSISTS = "player_assists"


SELECTION_OVER = "over"
SELECTION_UNDER = "under"
SELECTIONS = (SELECTION_OVER, SELECTION_UNDER)

_STAT_FIELD: dict[NbaPropMarket, str] = {
    NbaPropMarket.POINTS: "points",
    NbaPropMarket.REBOUNDS: "rebounds",
    NbaPropMarket.ASSISTS: "assists",
}


def actual_stat_value(log: NbaPlayerGameLog, market: str) -> float | None:
    """The box-score value a market settles on, or None when the market is
    unknown or the stat was not recorded."""
    try:
        field = _STAT_FIELD[NbaPropMarket(market)]
    except ValueError:
        return None
    value = getattr(log, field)
    return float(value) if value is not None else None


# The Odds API's market keys (verified against its official betting-markets
# documentation, 2026-10-02 — see app/providers/the_odds_api.py). The main
# line and the alternate-line ladder are separate keys; both map to the same
# market here and differ only in NbaPropQuote.is_alternate_line.
ODDS_API_MAIN_MARKET_KEYS: dict[str, NbaPropMarket] = {
    "player_points": NbaPropMarket.POINTS,
    "player_rebounds": NbaPropMarket.REBOUNDS,
    "player_assists": NbaPropMarket.ASSISTS,
}
ODDS_API_ALTERNATE_MARKET_KEYS: dict[str, NbaPropMarket] = {
    "player_points_alternate": NbaPropMarket.POINTS,
    "player_rebounds_alternate": NbaPropMarket.REBOUNDS,
    "player_assists_alternate": NbaPropMarket.ASSISTS,
}


def market_for_odds_api_key(market_key: str) -> tuple[NbaPropMarket, bool] | None:
    """(market, is_alternate_line) for a provider market key, or None for a
    key this project does not cover — reported as unsupported by ingestion,
    never silently mapped to something close."""
    if market_key in ODDS_API_MAIN_MARKET_KEYS:
        return ODDS_API_MAIN_MARKET_KEYS[market_key], False
    if market_key in ODDS_API_ALTERNATE_MARKET_KEYS:
        return ODDS_API_ALTERNATE_MARKET_KEYS[market_key], True
    return None
