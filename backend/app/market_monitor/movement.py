"""Market movement anomalies (item 5) — three genuinely different shapes
of "something changed," using the full historical OddsQuote/PlayerPropMarket
record (both tables are append-only, so every price ever observed for a
market is still there — see app/player_modelling/market_movement.py's
identical assumption). All movement is measured in IMPLIED-PROBABILITY
space (non-linear in decimal odds otherwise), and every threshold below is
an explicit, documented constant — never fit to outcomes (item 8's "do not
tune thresholds using future outcomes" applies to this module too, even
though it's about historical price movement, not settlement).

  - SHARP_MARKET_MOVE_MODEL_STABLE: the eligible-book consensus moved by
    more than SHARP_MOVE_PP between its first and latest observation,
    while nothing about the model's own belief changed in between (the
    model is a single current value here — this module has no access to a
    time series of past model probabilities, so "stable" means "the
    model's probability supplied by the caller is the one live figure,"
    documented rather than silently assumed).
  - BOOKMAKER_MOVED_VS_STABLE_CONSENSUS: one specific bookmaker's own
    price moved materially while the rest of the eligible market barely
    moved — a plausible sign that one book is repricing ahead of (or
    independently of) the rest of the market.
  - CONSENSUS_MOVED_VS_STALE_BOOKMAKER: the consensus moved materially
    while one specific bookmaker's price has not changed since it was
    first observed — a plausible sign that book simply hasn't caught up.
"""

from dataclasses import dataclass
from datetime import datetime

from app.edges.overround import implied_probability

# A move smaller than this in probability space is routine drift, not a
# "sharp" move — roughly a 1-tick price change on a mid-priced market.
SHARP_MOVE_PP = 0.05
# Below this, a bookmaker/consensus is considered "stable" (not moving) —
# deliberately smaller than SHARP_MOVE_PP so "one book moved, rest didn't"
# requires a real gap between the mover and the rest, not two numbers that
# both technically moved by slightly different small amounts.
STABLE_MOVE_PP = 0.02


@dataclass(frozen=True)
class BookmakerSeries:
    bookmaker_name: str
    first_price: float
    first_at: datetime
    latest_price: float
    latest_at: datetime

    @property
    def first_probability(self) -> float:
        return implied_probability(self.first_price)

    @property
    def latest_probability(self) -> float:
        return implied_probability(self.latest_price)

    @property
    def moved_pp(self) -> float:
        return abs(self.latest_probability - self.first_probability)


def build_bookmaker_series(quotes: list, *, bookmaker_name_by_id: dict[int, str]) -> list[BookmakerSeries]:
    """quotes: raw OddsQuote/PlayerPropMarket rows for ONE exact market
    (already filtered to the exact selection/threshold/line — never mixed
    across non-equivalent lines), any bookmaker, every historical snapshot."""
    by_bookmaker: dict[int, list] = {}
    for q in quotes:
        by_bookmaker.setdefault(q.bookmaker_id, []).append(q)
    series = []
    for bookmaker_id, rows in by_bookmaker.items():
        name = bookmaker_name_by_id.get(bookmaker_id)
        if name is None:
            continue
        rows_sorted = sorted(rows, key=lambda r: r.recorded_at)
        first, latest = rows_sorted[0], rows_sorted[-1]
        series.append(BookmakerSeries(bookmaker_name=name, first_price=first.price_decimal, first_at=first.recorded_at, latest_price=latest.price_decimal, latest_at=latest.recorded_at))
    return series


@dataclass(frozen=True)
class MovementFinding:
    kind: str  # "sharp_consensus_move" | "bookmaker_diverges" | "bookmaker_stale_vs_consensus"
    consensus_first_pp: float
    consensus_latest_pp: float
    bookmaker: BookmakerSeries | None


def detect_movement_anomalies(series: list[BookmakerSeries]) -> list[MovementFinding]:
    if len(series) < 2:
        return []  # need a real "rest of the market" to compare a single book against

    consensus_first = sum(s.first_probability for s in series) / len(series)
    consensus_latest = sum(s.latest_probability for s in series) / len(series)
    consensus_move = abs(consensus_latest - consensus_first)

    findings: list[MovementFinding] = []
    if consensus_move >= SHARP_MOVE_PP:
        findings.append(MovementFinding(kind="sharp_consensus_move", consensus_first_pp=consensus_first, consensus_latest_pp=consensus_latest, bookmaker=None))

    for s in series:
        if consensus_move < STABLE_MOVE_PP and s.moved_pp >= SHARP_MOVE_PP:
            findings.append(MovementFinding(kind="bookmaker_diverges", consensus_first_pp=consensus_first, consensus_latest_pp=consensus_latest, bookmaker=s))
        elif consensus_move >= SHARP_MOVE_PP and s.moved_pp < STABLE_MOVE_PP:
            findings.append(MovementFinding(kind="bookmaker_stale_vs_consensus", consensus_first_pp=consensus_first, consensus_latest_pp=consensus_latest, bookmaker=s))

    return findings
