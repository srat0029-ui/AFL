"""Market Movement Explorer — a read-only composition/analysis layer over
already-persisted bookmaker quote history (OddsQuote, PlayerPropMarket) and
model-side value history (ModelValueObservation), letting a genuinely
chosen match/market be inspected as a full time series rather than only the
latest summary movement app.player_modelling.market_movement.py /
model_movement.py already compute (both of those remain the source of
truth for "what's the current headline movement" elsewhere in the app;
this module answers "show me the whole history" for one specific
match/market a user has picked).

Nothing here is a new detection/pricing engine and nothing here writes new
data — every function only reads already-frozen rows. Consensus/de-vig
reuses the exact same app.edges.overround primitives and the same
eligibility/pairing rules as app.player_modelling.consensus_and_outliers,
generalised along the time axis with an explicit "as of" cutoff instead of
"as of now": at each checkpoint, a bookmaker's contribution is devigged
against that SAME bookmaker's own latest-at-or-before-that-instant quote
for the opposite side, so every pairing genuinely existed simultaneously to
what was actually known at that moment — never a same-book pairing
invented across an unrelated pair of timestamps.

Discipline enforced throughout (do not weaken without also updating the
frontend's language to match):
 - Bookmaker-quote and model-observation timestamps are NEVER aligned or
   interpolated onto a shared axis. Every point keeps its own genuine
   recorded_at.
 - Series are split strictly at match.scheduled_start: pre-kickoff points
   feed the movement figures; at-or-after-kickoff points are surfaced
   separately (never silently dropped, never blended into "movement").
 - "first observed" / "latest observed pre-kickoff" — never "closing
   line" — unless OddsQuote.is_closing_line is genuinely True on that row
   (PlayerPropMarket has no such flag at all, so it is never claimed there).
 - A consensus point is only ever returned when at least one eligible
   bookmaker quote genuinely exists at that instant; never fabricated.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.edges.overround import implied_probability, remove_overround
from app.models import (
    Match,
    ModelValueObservation,
    OddsQuote,
    Player,
    PlayerPropMarket,
    VALUE_PLAYER_DISPOSAL_PROBABILITY,
    VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    VALUE_PLAYER_GOAL_PROBABILITY,
    VALUE_PLAYER_GOAL_PROJECTED_MEAN,
    VALUE_TEAM_WIN_PROBABILITY,
)
from app.models.bookmaker import ELIGIBILITY_INCLUDED

TEAM_MARKET_TYPES = ("h2h", "line", "total")
PLAYER_MARKET_TYPES = ("player_disposals", "player_goals")

PLAYER_PROBABILITY_VALUE_TYPE = {
    "player_disposals": VALUE_PLAYER_DISPOSAL_PROBABILITY,
    "player_goals": VALUE_PLAYER_GOAL_PROBABILITY,
}
PLAYER_PROJECTED_MEAN_VALUE_TYPE = {
    "player_disposals": VALUE_PLAYER_DISPOSAL_PROJECTED_MEAN,
    "player_goals": VALUE_PLAYER_GOAL_PROJECTED_MEAN,
}

METHODOLOGY_NOTES = [
    "Bookmaker-quote and model-observation timestamps are never aligned or interpolated onto a shared axis — "
    "every point keeps its own genuinely recorded timestamp.",
    "Consensus probability is a simple, unweighted mean of eligible (non-exchange, non-excluded) bookmakers' own "
    "probability at that instant. Same-bookmaker de-vig is applied only where that exact bookmaker's opposite-side "
    "price is also known at or before that instant; raw implied probability is used otherwise, and handicap "
    "(\"line\") markets are never de-vigged (the opposite side carries a different, non-mirrored line value).",
    "\"First observed\" and \"latest observed pre-kickoff\" describe when a price was captured, not an official "
    "bookmaker closing line — that label is only ever used where the underlying quote is itself flagged as one.",
    "Observations at or after kickoff are shown separately and excluded from every pre-match movement figure.",
    "A lineup-status change coinciding with a model-value change is a fact about what was observed together, "
    "never a claim that one caused the other.",
]


def _aware(dt: datetime) -> datetime:
    """SQLite round-trips DateTime(timezone=True) values as naive even
    though the data is genuinely UTC throughout (documented already in
    app/market_monitor/prospective_coverage.py and
    app/player_modelling/real_market_tracking.py) — normalise once here so
    every Python-level comparison/arithmetic below is safe regardless of
    dialect."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def hours_to_kickoff(recorded_at: datetime, scheduled_start: datetime) -> float:
    return (_aware(scheduled_start) - _aware(recorded_at)).total_seconds() / 3600.0


def _split_pre_post_kickoff(rows: list, recorded_at_fn, scheduled_start: datetime) -> tuple[list, list]:
    pre, post = [], []
    for r in rows:
        (pre if _aware(recorded_at_fn(r)) < _aware(scheduled_start) else post).append(r)
    return pre, post


# --- Discovery ---------------------------------------------------------------


@dataclass(frozen=True)
class MatchContext:
    match_id: int
    home_team: str
    away_team: str
    scheduled_start: datetime
    status: str


def _match_context(match: Match) -> MatchContext:
    return MatchContext(
        match_id=match.id, home_team=match.home_team.name, away_team=match.away_team.name,
        scheduled_start=match.scheduled_start, status=match.status.value,
    )


@dataclass(frozen=True)
class MatchWithHistory:
    match: MatchContext
    n_odds_quotes: int
    n_player_prop_quotes: int
    n_model_observations: int
    earliest_observed_at: datetime | None
    latest_observed_at: datetime | None


def _aggregate_by_match(db: Session, model) -> dict[int, tuple[int, datetime | None, datetime | None]]:
    rows = db.execute(
        select(model.match_id, func.count(), func.min(model.recorded_at), func.max(model.recorded_at)).group_by(model.match_id)
    ).all()
    return {match_id: (count, earliest, latest) for match_id, count, earliest, latest in rows}


def list_matches_with_history(db: Session, *, limit: int = 200) -> list[MatchWithHistory]:
    """Every match that has ANY genuine movement history — OddsQuote,
    PlayerPropMarket, or ModelValueObservation rows — sorted by most
    recently scheduled first. Purely a discovery aid; it decides nothing
    about which matches are "interesting", just which ones have data to
    show."""
    odds = _aggregate_by_match(db, OddsQuote)
    props = _aggregate_by_match(db, PlayerPropMarket)
    model_obs = _aggregate_by_match(db, ModelValueObservation)
    match_ids = set(odds) | set(props) | set(model_obs)
    if not match_ids:
        return []

    matches = db.scalars(select(Match).where(Match.id.in_(match_ids))).all()
    results = []
    for m in matches:
        o = odds.get(m.id, (0, None, None))
        p = props.get(m.id, (0, None, None))
        mo = model_obs.get(m.id, (0, None, None))
        timestamps = [t for t in (o[1], o[2], p[1], p[2], mo[1], mo[2]) if t is not None]
        results.append(MatchWithHistory(
            match=_match_context(m), n_odds_quotes=o[0], n_player_prop_quotes=p[0], n_model_observations=mo[0],
            earliest_observed_at=min(timestamps) if timestamps else None,
            latest_observed_at=max(timestamps) if timestamps else None,
        ))
    results.sort(key=lambda r: r.match.scheduled_start, reverse=True)
    return results[:limit]


@dataclass(frozen=True)
class TeamMarketOption:
    market_type: str
    selection: str
    line_value: float | None
    n_quotes: int
    bookmakers: list[str]
    first_observed_at: datetime
    latest_observed_at: datetime
    has_model_series: bool  # only ever true for h2h — team win probability is the only tracked comparable value


@dataclass(frozen=True)
class PlayerMarketOption:
    player_id: int
    player_name: str
    market_type: str
    line_type: str
    threshold: float
    n_quotes: int
    bookmakers: list[str]
    first_observed_at: datetime
    latest_observed_at: datetime
    has_model_series: bool


@dataclass(frozen=True)
class MatchMarketOptions:
    match: MatchContext
    team_markets: list[TeamMarketOption]
    player_markets: list[PlayerMarketOption]


def list_market_options(db: Session, *, match_id: int) -> MatchMarketOptions | None:
    match = db.get(Match, match_id)
    if match is None:
        return None

    team_quotes = db.scalars(select(OddsQuote).where(OddsQuote.match_id == match_id)).all()
    team_groups: dict[tuple, list[OddsQuote]] = defaultdict(list)
    for q in team_quotes:
        team_groups[(q.market_type, q.selection, q.line_value)].append(q)
    team_model_selections = {
        r.selection
        for r in db.scalars(
            select(ModelValueObservation).where(
                ModelValueObservation.match_id == match_id, ModelValueObservation.value_type == VALUE_TEAM_WIN_PROBABILITY
            )
        ).all()
    }
    team_markets = []
    for (market_type, selection, line_value), qs in team_groups.items():
        qs_sorted = sorted(qs, key=lambda q: q.recorded_at)
        team_markets.append(TeamMarketOption(
            market_type=market_type, selection=selection, line_value=line_value, n_quotes=len(qs),
            bookmakers=sorted({q.bookmaker.name for q in qs}),
            first_observed_at=qs_sorted[0].recorded_at, latest_observed_at=qs_sorted[-1].recorded_at,
            has_model_series=(market_type == "h2h" and selection in team_model_selections),
        ))
    team_markets.sort(key=lambda o: (o.market_type, o.selection))

    prop_quotes = db.scalars(
        select(PlayerPropMarket).where(PlayerPropMarket.match_id == match_id, PlayerPropMarket.selection.in_(["over", "yes", None]))
    ).all()
    prop_groups: dict[tuple, list[PlayerPropMarket]] = defaultdict(list)
    for q in prop_quotes:
        prop_groups[(q.player_id, q.market_type, q.line_type, q.threshold)].append(q)
    player_model_keys = {
        (r.player_id, r.value_type, r.threshold)
        for r in db.scalars(
            select(ModelValueObservation).where(ModelValueObservation.match_id == match_id, ModelValueObservation.player_id.is_not(None))
        ).all()
    }
    player_markets = []
    for (player_id, market_type, line_type, threshold), qs in prop_groups.items():
        qs_sorted = sorted(qs, key=lambda q: q.recorded_at)
        value_type = PLAYER_PROBABILITY_VALUE_TYPE.get(market_type)
        player_markets.append(PlayerMarketOption(
            player_id=player_id, player_name=qs_sorted[0].player.display_name, market_type=market_type,
            line_type=line_type, threshold=threshold, n_quotes=len(qs),
            bookmakers=sorted({q.bookmaker.name for q in qs}),
            first_observed_at=qs_sorted[0].recorded_at, latest_observed_at=qs_sorted[-1].recorded_at,
            has_model_series=(value_type is not None and (player_id, value_type, threshold) in player_model_keys),
        ))
    player_markets.sort(key=lambda o: (o.player_name, o.market_type, o.threshold))

    return MatchMarketOptions(match=_match_context(match), team_markets=team_markets, player_markets=player_markets)


# --- Detailed series -----------------------------------------------------


@dataclass(frozen=True)
class BookmakerQuotePoint:
    bookmaker_name: str
    price_decimal: float
    raw_implied_probability: float
    recorded_at: datetime
    hours_to_kickoff: float
    source: str
    is_closing_line: bool


@dataclass(frozen=True)
class ConsensusPoint:
    as_of: datetime
    hours_to_kickoff: float
    consensus_probability: float
    n_bookmakers: int
    n_devigged: int
    spread: float


@dataclass(frozen=True)
class ModelObservationPoint:
    value_type: str
    value_kind: str
    value: float
    model_name: str
    model_version: str
    recorded_at: datetime
    hours_to_kickoff: float
    lineup_status: str | None


@dataclass(frozen=True)
class LineupStatusChange:
    from_status: str | None
    to_status: str | None
    changed_at: datetime
    hours_to_kickoff: float
    value_changed_at_same_observation: bool
    value_before: float
    value_after: float


@dataclass(frozen=True)
class SeriesEndpoint:
    probability: float
    recorded_at: datetime
    hours_to_kickoff: float
    price_decimal: float | None  # None for consensus/model points
    bookmaker_name: str | None  # None for consensus/model points


@dataclass(frozen=True)
class LargestMovement:
    from_probability: float
    to_probability: float
    absolute_change: float
    at: datetime
    hours_to_kickoff: float


@dataclass(frozen=True)
class SeriesSummary:
    label: str
    n_observations: int
    insufficient_history: bool  # fewer than 2 observations — no movement can be described
    first_observed: SeriesEndpoint | None
    latest_observed_pre_kickoff: SeriesEndpoint | None
    total_probability_change: float | None
    largest_single_movement: LargestMovement | None


def _series_summary(label: str, points: list[SeriesEndpoint]) -> SeriesSummary:
    n = len(points)
    if n == 0:
        return SeriesSummary(
            label=label, n_observations=0, insufficient_history=True, first_observed=None,
            latest_observed_pre_kickoff=None, total_probability_change=None, largest_single_movement=None,
        )
    ordered = sorted(points, key=lambda p: p.recorded_at)
    first, latest = ordered[0], ordered[-1]
    largest: LargestMovement | None = None
    for prev, cur in zip(ordered, ordered[1:]):
        change = cur.probability - prev.probability
        if largest is None or abs(change) > abs(largest.absolute_change):
            largest = LargestMovement(
                from_probability=prev.probability, to_probability=cur.probability, absolute_change=change,
                at=cur.recorded_at, hours_to_kickoff=cur.hours_to_kickoff,
            )
    return SeriesSummary(
        label=label, n_observations=n, insufficient_history=n < 2, first_observed=first,
        latest_observed_pre_kickoff=latest,
        total_probability_change=(latest.probability - first.probability) if n >= 2 else None,
        largest_single_movement=largest,
    )


def _quote_endpoint(p: BookmakerQuotePoint) -> SeriesEndpoint:
    return SeriesEndpoint(probability=p.raw_implied_probability, recorded_at=p.recorded_at, hours_to_kickoff=p.hours_to_kickoff, price_decimal=p.price_decimal, bookmaker_name=p.bookmaker_name)


def _consensus_endpoint(c: ConsensusPoint) -> SeriesEndpoint:
    return SeriesEndpoint(probability=c.consensus_probability, recorded_at=c.as_of, hours_to_kickoff=c.hours_to_kickoff, price_decimal=None, bookmaker_name=None)


def _model_endpoint(o: ModelObservationPoint) -> SeriesEndpoint:
    return SeriesEndpoint(probability=o.value, recorded_at=o.recorded_at, hours_to_kickoff=o.hours_to_kickoff, price_decimal=None, bookmaker_name=None)


def _model_point(o: ModelValueObservation, scheduled_start: datetime) -> ModelObservationPoint:
    return ModelObservationPoint(
        value_type=o.value_type, value_kind=o.value_kind, value=o.value, model_name=o.model_name,
        model_version=o.model_version, recorded_at=o.recorded_at,
        hours_to_kickoff=hours_to_kickoff(o.recorded_at, scheduled_start), lineup_status=o.lineup_status,
    )


def _lineup_status_changes(observations: list[ModelValueObservation], scheduled_start: datetime) -> list[LineupStatusChange]:
    """Diffs CONSECUTIVE observations of the same tracked identity for a
    lineup_status change — model_value_observations.py writes a new row
    whenever EITHER the value OR the lineup_status changes, so a status
    change and a value change can land on the same row or different ones;
    `value_changed_at_same_observation` reports which, honestly, without
    implying either caused the other."""
    changes = []
    prev: ModelValueObservation | None = None
    for obs in observations:
        if prev is not None and prev.lineup_status != obs.lineup_status:
            changes.append(LineupStatusChange(
                from_status=prev.lineup_status, to_status=obs.lineup_status, changed_at=obs.recorded_at,
                hours_to_kickoff=hours_to_kickoff(obs.recorded_at, scheduled_start),
                value_changed_at_same_observation=(prev.value != obs.value),
                value_before=prev.value, value_after=obs.value,
            ))
        prev = obs
    return changes


def _consensus_series(
    *, quotes_by_bookmaker_side: dict[tuple[int, str], list], eligible_bookmaker_ids: set[int], checkpoints: list[datetime],
    primary_side: str, opposite_side: str | None, scheduled_start: datetime,
) -> list[ConsensusPoint]:
    def _as_of(bookmaker_id: int, side: str, t: datetime):
        candidates = [q for q in quotes_by_bookmaker_side.get((bookmaker_id, side), []) if _aware(q.recorded_at) <= _aware(t)]
        return candidates[-1] if candidates else None

    points = []
    for t in checkpoints:
        entries: list[tuple[float, bool]] = []
        for bookmaker_id in eligible_bookmaker_ids:
            this_q = _as_of(bookmaker_id, primary_side, t)
            if this_q is None:
                continue
            opp_q = _as_of(bookmaker_id, opposite_side, t) if opposite_side else None
            if opp_q is not None:
                fair = remove_overround({"this": this_q.price_decimal, "other": opp_q.price_decimal})
                entries.append((fair["this"], True))
            else:
                entries.append((implied_probability(this_q.price_decimal), False))
        if not entries:
            continue
        probs = [e[0] for e in entries]
        points.append(ConsensusPoint(
            as_of=t, hours_to_kickoff=hours_to_kickoff(t, scheduled_start), consensus_probability=sum(probs) / len(probs),
            n_bookmakers=len(entries), n_devigged=sum(1 for e in entries if e[1]), spread=max(probs) - min(probs),
        ))
    return points


@dataclass(frozen=True)
class MarketMovementSeries:
    match: MatchContext
    identity_label: str
    methodology_notes: list[str]
    bookmaker_quotes: list[BookmakerQuotePoint]  # pre-kickoff only
    bookmaker_quotes_post_kickoff: list[BookmakerQuotePoint]
    consensus_series: list[ConsensusPoint]  # pre-kickoff only, eligible bookmakers only
    model_observations: list[ModelObservationPoint]  # pre-kickoff only
    model_observations_post_kickoff: list[ModelObservationPoint]
    model_projected_mean_observations: list[ModelObservationPoint]  # secondary series, player markets only
    lineup_status_changes: list[LineupStatusChange]
    bookmaker_summary: SeriesSummary
    consensus_summary: SeriesSummary | None
    model_summary: SeriesSummary | None


def load_team_market_series(db: Session, *, match_id: int, market_type: str, selection: str, line_value: float | None) -> MarketMovementSeries | None:
    match = db.get(Match, match_id)
    if match is None:
        return None

    quotes = db.scalars(
        select(OddsQuote)
        .where(OddsQuote.match_id == match_id, OddsQuote.market_type == market_type, OddsQuote.selection == selection, OddsQuote.line_value == line_value)
        .order_by(OddsQuote.recorded_at)
    ).all()
    pre_quotes, post_quotes = _split_pre_post_kickoff(quotes, lambda q: q.recorded_at, match.scheduled_start)
    quote_points_pre = [_odds_quote_point(q, match.scheduled_start) for q in pre_quotes]
    quote_points_post = [_odds_quote_point(q, match.scheduled_start) for q in post_quotes]

    consensus_points: list[ConsensusPoint] = []
    if pre_quotes:
        opposite_selection: str | None = None
        if market_type == "h2h" and selection in (match.home_team.name, match.away_team.name):
            opposite_selection = match.away_team.name if selection == match.home_team.name else match.home_team.name
        elif market_type == "total":
            opposite_selection = "under" if selection == "over" else "over"
        # "line" is never devigged — opposite_selection stays None.

        # Same market instance only (h2h always line_value None; total/line
        # scoped to the SAME line_value, since the opposite side of "total
        # 150.5" is "under 150.5", not a different line).
        all_same_market = db.scalars(
            select(OddsQuote).where(OddsQuote.match_id == match_id, OddsQuote.market_type == market_type, OddsQuote.line_value == line_value)
        ).all()
        by_bookmaker_side: dict[tuple[int, str], list[OddsQuote]] = defaultdict(list)
        for q in all_same_market:
            by_bookmaker_side[(q.bookmaker_id, q.selection)].append(q)
        for lst in by_bookmaker_side.values():
            lst.sort(key=lambda q: q.recorded_at)

        eligible_bookmaker_ids = {q.bookmaker_id for q in pre_quotes if q.bookmaker.eligibility == ELIGIBILITY_INCLUDED}
        checkpoints = sorted({q.recorded_at for q in pre_quotes if q.bookmaker_id in eligible_bookmaker_ids})
        consensus_points = _consensus_series(
            quotes_by_bookmaker_side=by_bookmaker_side, eligible_bookmaker_ids=eligible_bookmaker_ids, checkpoints=checkpoints,
            primary_side=selection, opposite_side=opposite_selection, scheduled_start=match.scheduled_start,
        )

    model_obs: list[ModelValueObservation] = []
    if market_type == "h2h":
        model_obs = list(db.scalars(
            select(ModelValueObservation)
            .where(ModelValueObservation.match_id == match_id, ModelValueObservation.value_type == VALUE_TEAM_WIN_PROBABILITY, ModelValueObservation.selection == selection)
            .order_by(ModelValueObservation.recorded_at)
        ).all())
    model_pre, model_post = _split_pre_post_kickoff(model_obs, lambda o: o.recorded_at, match.scheduled_start)
    model_points_pre = [_model_point(o, match.scheduled_start) for o in model_pre]
    model_points_post = [_model_point(o, match.scheduled_start) for o in model_post]

    line_label = f" ({line_value:+g})" if line_value is not None else ""
    return MarketMovementSeries(
        match=_match_context(match), identity_label=f"{selection} — {market_type}{line_label}",
        methodology_notes=METHODOLOGY_NOTES,
        bookmaker_quotes=quote_points_pre, bookmaker_quotes_post_kickoff=quote_points_post,
        consensus_series=consensus_points,
        model_observations=model_points_pre, model_observations_post_kickoff=model_points_post,
        model_projected_mean_observations=[],
        lineup_status_changes=[],  # team-level ModelValueObservation rows never carry a lineup_status
        bookmaker_summary=_series_summary("Bookmaker quotes (all bookmakers)", [_quote_endpoint(p) for p in quote_points_pre]),
        consensus_summary=_series_summary("Bookmaker consensus", [_consensus_endpoint(c) for c in consensus_points]) if consensus_points else None,
        model_summary=_series_summary("Model probability", [_model_endpoint(o) for o in model_points_pre]) if model_points_pre else None,
    )


def _odds_quote_point(q: OddsQuote, scheduled_start: datetime) -> BookmakerQuotePoint:
    return BookmakerQuotePoint(
        bookmaker_name=q.bookmaker.name, price_decimal=q.price_decimal, raw_implied_probability=implied_probability(q.price_decimal),
        recorded_at=q.recorded_at, hours_to_kickoff=hours_to_kickoff(q.recorded_at, scheduled_start), source=q.source, is_closing_line=q.is_closing_line,
    )


def _prop_quote_point(q: PlayerPropMarket, scheduled_start: datetime) -> BookmakerQuotePoint:
    return BookmakerQuotePoint(
        bookmaker_name=q.bookmaker.name, price_decimal=q.price_decimal, raw_implied_probability=implied_probability(q.price_decimal),
        recorded_at=q.recorded_at, hours_to_kickoff=hours_to_kickoff(q.recorded_at, scheduled_start), source=q.source,
        is_closing_line=False,  # PlayerPropMarket carries no such flag — never claimed
    )


def load_player_market_series(
    db: Session, *, match_id: int, player_id: int, market_type: str, line_type: str, threshold: float
) -> MarketMovementSeries | None:
    match = db.get(Match, match_id)
    player = db.get(Player, player_id)
    if match is None or player is None:
        return None

    quotes = db.scalars(
        select(PlayerPropMarket)
        .where(
            PlayerPropMarket.match_id == match_id, PlayerPropMarket.player_id == player_id, PlayerPropMarket.market_type == market_type,
            PlayerPropMarket.line_type == line_type, PlayerPropMarket.threshold == threshold, PlayerPropMarket.selection.in_(["over", "yes", None]),
        )
        .order_by(PlayerPropMarket.recorded_at)
    ).all()
    pre_quotes, post_quotes = _split_pre_post_kickoff(quotes, lambda q: q.recorded_at, match.scheduled_start)
    quote_points_pre = [_prop_quote_point(q, match.scheduled_start) for q in pre_quotes]
    quote_points_post = [_prop_quote_point(q, match.scheduled_start) for q in post_quotes]

    consensus_points: list[ConsensusPoint] = []
    if pre_quotes:
        primary_side = "yes" if line_type == "multi_plus" else "over"
        opposite_side = "under" if line_type == "over_under" else None

        all_same_market = db.scalars(
            select(PlayerPropMarket).where(
                PlayerPropMarket.match_id == match_id, PlayerPropMarket.player_id == player_id,
                PlayerPropMarket.market_type == market_type, PlayerPropMarket.line_type == line_type, PlayerPropMarket.threshold == threshold,
            )
        ).all()
        by_bookmaker_side: dict[tuple[int, str], list[PlayerPropMarket]] = defaultdict(list)
        for q in all_same_market:
            by_bookmaker_side[(q.bookmaker_id, q.selection or "over")].append(q)
        for lst in by_bookmaker_side.values():
            lst.sort(key=lambda q: q.recorded_at)

        eligible_bookmaker_ids = {q.bookmaker_id for q in pre_quotes if q.bookmaker.eligibility == ELIGIBILITY_INCLUDED}
        checkpoints = sorted({q.recorded_at for q in pre_quotes if q.bookmaker_id in eligible_bookmaker_ids})
        consensus_points = _consensus_series(
            quotes_by_bookmaker_side=by_bookmaker_side, eligible_bookmaker_ids=eligible_bookmaker_ids, checkpoints=checkpoints,
            primary_side=primary_side, opposite_side=opposite_side, scheduled_start=match.scheduled_start,
        )

    prob_value_type = PLAYER_PROBABILITY_VALUE_TYPE.get(market_type)
    model_obs: list[ModelValueObservation] = []
    if prob_value_type is not None:
        model_obs = list(db.scalars(
            select(ModelValueObservation)
            .where(
                ModelValueObservation.match_id == match_id, ModelValueObservation.player_id == player_id,
                ModelValueObservation.value_type == prob_value_type, ModelValueObservation.threshold == threshold,
            )
            .order_by(ModelValueObservation.recorded_at)
        ).all())
    model_pre, model_post = _split_pre_post_kickoff(model_obs, lambda o: o.recorded_at, match.scheduled_start)
    model_points_pre = [_model_point(o, match.scheduled_start) for o in model_pre]
    model_points_post = [_model_point(o, match.scheduled_start) for o in model_post]

    mean_value_type = PLAYER_PROJECTED_MEAN_VALUE_TYPE.get(market_type)
    mean_points_pre: list[ModelObservationPoint] = []
    if mean_value_type is not None:
        mean_obs = list(db.scalars(
            select(ModelValueObservation)
            .where(
                ModelValueObservation.match_id == match_id, ModelValueObservation.player_id == player_id,
                ModelValueObservation.value_type == mean_value_type, ModelValueObservation.threshold.is_(None),
                ModelValueObservation.recorded_at < match.scheduled_start,
            )
            .order_by(ModelValueObservation.recorded_at)
        ).all())
        mean_points_pre = [_model_point(o, match.scheduled_start) for o in mean_obs]

    lineup_source = model_pre if model_pre else db.scalars(
        select(ModelValueObservation)
        .where(
            ModelValueObservation.match_id == match_id, ModelValueObservation.player_id == player_id,
            ModelValueObservation.value_type == mean_value_type, ModelValueObservation.threshold.is_(None),
        )
        .order_by(ModelValueObservation.recorded_at)
    ).all() if mean_value_type is not None else []
    lineup_changes = _lineup_status_changes(lineup_source, match.scheduled_start)

    threshold_label = f"{threshold:g}+" if line_type == "multi_plus" else f"{threshold:g}"
    return MarketMovementSeries(
        match=_match_context(match), identity_label=f"{player.display_name} — {market_type} {threshold_label}",
        methodology_notes=METHODOLOGY_NOTES,
        bookmaker_quotes=quote_points_pre, bookmaker_quotes_post_kickoff=quote_points_post,
        consensus_series=consensus_points,
        model_observations=model_points_pre, model_observations_post_kickoff=model_points_post,
        model_projected_mean_observations=mean_points_pre,
        lineup_status_changes=lineup_changes,
        bookmaker_summary=_series_summary("Bookmaker quotes (all bookmakers)", [_quote_endpoint(p) for p in quote_points_pre]),
        consensus_summary=_series_summary("Bookmaker consensus", [_consensus_endpoint(c) for c in consensus_points]) if consensus_points else None,
        model_summary=_series_summary("Model probability", [_model_endpoint(o) for o in model_points_pre]) if model_points_pre else None,
    )
