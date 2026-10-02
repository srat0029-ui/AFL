"""Sport-agnostic line settlement: the result vocabulary and the arithmetic
for "did this actual value clear this line". Pure functions, no DB access.

Lifted unchanged out of app/player_modelling/prop_settlement.py (AFL), which
now delegates here, so an AFL disposals leg and an NBA points prop are
adjudicated by literally the same code. What a sport still owns is turning
its own box score into `actual` (AFL: PlayerMatchStat.disposals; NBA:
NbaPlayerGameLog.points) and deciding when a selection is void (e.g. the
player did not play) — neither is knowable here.
"""

RESULT_WON = "won"
RESULT_LOST = "lost"
RESULT_PUSH = "push"
RESULT_VOID = "void"
RESULT_UNRESOLVED = "unresolved"

LINE_TYPE_OVER_UNDER = "over_under"  # e.g. over/under 27.5
LINE_TYPE_MULTI_PLUS = "multi_plus"  # e.g. 20+ ("N or more")

SELECTION_OVER = "over"
SELECTION_UNDER = "under"
SELECTION_YES = "yes"


def settle_line(actual: float, threshold: float, line_type: str) -> str:
    """Result for the OVER (or, for multi_plus, the "yes") side of a line.

    over_under is a strict inequality so it stays correct for a
    whole-number threshold, where actual == threshold is a genuine push
    (impossible on a .5 line, but nothing here assumes every line is .5).
    multi_plus ("N+") is an inclusive lower bound, so it cannot push.
    """
    if line_type == LINE_TYPE_OVER_UNDER:
        if actual > threshold:
            return RESULT_WON
        if actual < threshold:
            return RESULT_LOST
        return RESULT_PUSH
    if line_type == LINE_TYPE_MULTI_PLUS:
        return RESULT_WON if actual >= threshold else RESULT_LOST
    return RESULT_UNRESOLVED


def settle_selection(actual: float, threshold: float, line_type: str, selection: str) -> str:
    """Result for a specific side of a line. "over"/"yes" is settle_line
    itself; "under" is its mirror (a push stays a push). Any other
    combination — including "under" on a multi_plus line, which no
    bookmaker offers — is unresolved rather than guessed."""
    over_result = settle_line(actual, threshold, line_type)
    if selection in (SELECTION_OVER, SELECTION_YES):
        return over_result
    if selection == SELECTION_UNDER and line_type == LINE_TYPE_OVER_UNDER:
        if over_result == RESULT_WON:
            return RESULT_LOST
        if over_result == RESULT_LOST:
            return RESULT_WON
        return over_result
    return RESULT_UNRESOLVED
