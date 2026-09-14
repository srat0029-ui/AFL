"""Market Movement Explorer API (/api/v1/market-movement/*) — read-only.
See app/player_modelling/market_movement_series.py for the composition
layer; this router only shapes its output into response models, never
computes anything itself.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.market_movement_schemas import (
    BookmakerQuotePointRead,
    ConsensusPointRead,
    LargestMovementRead,
    LineupStatusChangeRead,
    MatchContextRead,
    MatchMarketOptionsRead,
    MatchWithHistoryRead,
    MarketMovementSeriesRead,
    ModelObservationPointRead,
    PlayerMarketOptionRead,
    SeriesEndpointRead,
    SeriesSummaryRead,
    TeamMarketOptionRead,
)
from app.database import get_db
from app.player_modelling.market_movement_series import (
    BookmakerQuotePoint,
    ConsensusPoint,
    LargestMovement,
    LineupStatusChange,
    MarketMovementSeries,
    MatchContext,
    MatchMarketOptions,
    MatchWithHistory,
    ModelObservationPoint,
    SeriesEndpoint,
    SeriesSummary,
    list_market_options,
    list_matches_with_history,
    load_player_market_series,
    load_team_market_series,
)

router = APIRouter(prefix="/api/v1/market-movement", tags=["market-movement-v1"])


def _match_context_read(m: MatchContext) -> MatchContextRead:
    return MatchContextRead(match_id=m.match_id, home_team=m.home_team, away_team=m.away_team, scheduled_start=m.scheduled_start, status=m.status)


@router.get("/matches", response_model=list[MatchWithHistoryRead])
def get_matches_with_history(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)) -> list[MatchWithHistoryRead]:
    rows: list[MatchWithHistory] = list_matches_with_history(db, limit=limit)
    return [
        MatchWithHistoryRead(
            match=_match_context_read(r.match), n_odds_quotes=r.n_odds_quotes, n_player_prop_quotes=r.n_player_prop_quotes,
            n_model_observations=r.n_model_observations, earliest_observed_at=r.earliest_observed_at, latest_observed_at=r.latest_observed_at,
        )
        for r in rows
    ]


@router.get("/matches/{match_id}/markets", response_model=MatchMarketOptionsRead)
def get_market_options(match_id: int, db: Session = Depends(get_db)) -> MatchMarketOptionsRead:
    options: MatchMarketOptions | None = list_market_options(db, match_id=match_id)
    if options is None:
        raise HTTPException(status_code=404, detail="match not found")
    return MatchMarketOptionsRead(
        match=_match_context_read(options.match),
        team_markets=[
            TeamMarketOptionRead(
                market_type=t.market_type, selection=t.selection, line_value=t.line_value, n_quotes=t.n_quotes,
                bookmakers=t.bookmakers, first_observed_at=t.first_observed_at, latest_observed_at=t.latest_observed_at,
                has_model_series=t.has_model_series,
            )
            for t in options.team_markets
        ],
        player_markets=[
            PlayerMarketOptionRead(
                player_id=p.player_id, player_name=p.player_name, market_type=p.market_type, line_type=p.line_type,
                threshold=p.threshold, n_quotes=p.n_quotes, bookmakers=p.bookmakers, first_observed_at=p.first_observed_at,
                latest_observed_at=p.latest_observed_at, has_model_series=p.has_model_series,
            )
            for p in options.player_markets
        ],
    )


def _quote_point_read(p: BookmakerQuotePoint) -> BookmakerQuotePointRead:
    return BookmakerQuotePointRead(
        bookmaker_name=p.bookmaker_name, price_decimal=p.price_decimal, raw_implied_probability=p.raw_implied_probability,
        recorded_at=p.recorded_at, hours_to_kickoff=p.hours_to_kickoff, source=p.source, is_closing_line=p.is_closing_line,
    )


def _consensus_point_read(c: ConsensusPoint) -> ConsensusPointRead:
    return ConsensusPointRead(
        as_of=c.as_of, hours_to_kickoff=c.hours_to_kickoff, consensus_probability=c.consensus_probability,
        n_bookmakers=c.n_bookmakers, n_devigged=c.n_devigged, spread=c.spread,
    )


def _model_point_read(o: ModelObservationPoint) -> ModelObservationPointRead:
    return ModelObservationPointRead(
        value_type=o.value_type, value_kind=o.value_kind, value=o.value, model_name=o.model_name, model_version=o.model_version,
        recorded_at=o.recorded_at, hours_to_kickoff=o.hours_to_kickoff, lineup_status=o.lineup_status,
    )


def _lineup_change_read(c: LineupStatusChange) -> LineupStatusChangeRead:
    return LineupStatusChangeRead(
        from_status=c.from_status, to_status=c.to_status, changed_at=c.changed_at, hours_to_kickoff=c.hours_to_kickoff,
        value_changed_at_same_observation=c.value_changed_at_same_observation, value_before=c.value_before, value_after=c.value_after,
    )


def _endpoint_read(e: SeriesEndpoint | None) -> SeriesEndpointRead | None:
    if e is None:
        return None
    return SeriesEndpointRead(probability=e.probability, recorded_at=e.recorded_at, hours_to_kickoff=e.hours_to_kickoff, price_decimal=e.price_decimal, bookmaker_name=e.bookmaker_name)


def _largest_movement_read(m: LargestMovement | None) -> LargestMovementRead | None:
    if m is None:
        return None
    return LargestMovementRead(from_probability=m.from_probability, to_probability=m.to_probability, absolute_change=m.absolute_change, at=m.at, hours_to_kickoff=m.hours_to_kickoff)


def _summary_read(s: SeriesSummary | None) -> SeriesSummaryRead | None:
    if s is None:
        return None
    return SeriesSummaryRead(
        label=s.label, n_observations=s.n_observations, insufficient_history=s.insufficient_history,
        first_observed=_endpoint_read(s.first_observed), latest_observed_pre_kickoff=_endpoint_read(s.latest_observed_pre_kickoff),
        total_probability_change=s.total_probability_change, largest_single_movement=_largest_movement_read(s.largest_single_movement),
    )


def _series_read(series: MarketMovementSeries) -> MarketMovementSeriesRead:
    return MarketMovementSeriesRead(
        match=_match_context_read(series.match), identity_label=series.identity_label, methodology_notes=series.methodology_notes,
        bookmaker_quotes=[_quote_point_read(p) for p in series.bookmaker_quotes],
        bookmaker_quotes_post_kickoff=[_quote_point_read(p) for p in series.bookmaker_quotes_post_kickoff],
        consensus_series=[_consensus_point_read(c) for c in series.consensus_series],
        model_observations=[_model_point_read(o) for o in series.model_observations],
        model_observations_post_kickoff=[_model_point_read(o) for o in series.model_observations_post_kickoff],
        model_projected_mean_observations=[_model_point_read(o) for o in series.model_projected_mean_observations],
        lineup_status_changes=[_lineup_change_read(c) for c in series.lineup_status_changes],
        bookmaker_summary=_summary_read(series.bookmaker_summary),
        consensus_summary=_summary_read(series.consensus_summary),
        model_summary=_summary_read(series.model_summary),
    )


@router.get("/series", response_model=MarketMovementSeriesRead)
def get_market_movement_series(
    match_id: int,
    identity_type: str = Query(..., pattern="^(team|player)$"),
    market_type: str = Query(...),
    selection: str | None = Query(default=None, description="Team markets: team name (h2h/line) or over/under (total)."),
    line_value: float | None = Query(default=None, description="Team markets: handicap/total line."),
    player_id: int | None = Query(default=None, description="Player markets only."),
    line_type: str | None = Query(default=None, description="Player markets only: over_under | multi_plus."),
    threshold: float | None = Query(default=None, description="Player markets only."),
    db: Session = Depends(get_db),
) -> MarketMovementSeriesRead:
    if identity_type == "team":
        if selection is None:
            raise HTTPException(status_code=422, detail="selection is required for identity_type=team")
        series = load_team_market_series(db, match_id=match_id, market_type=market_type, selection=selection, line_value=line_value)
    else:
        if player_id is None or line_type is None or threshold is None:
            raise HTTPException(status_code=422, detail="player_id, line_type and threshold are required for identity_type=player")
        series = load_player_market_series(db, match_id=match_id, player_id=player_id, market_type=market_type, line_type=line_type, threshold=threshold)

    if series is None:
        raise HTTPException(status_code=404, detail="match not found")
    return _series_read(series)
