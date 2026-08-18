from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BookmakerEligibilityUpdate, BookmakerRead, MarketType, OddsQuoteCreate, OddsQuoteRead
from app.database import get_db
from app.models import Bookmaker, Match, OddsQuote
from app.player_modelling.bookmaker_classification import VALID_ELIGIBILITIES, set_bookmaker_eligibility

odds_router = APIRouter(prefix="/api/matches/{match_id}/odds", tags=["odds"])
bookmakers_router = APIRouter(prefix="/api/bookmakers", tags=["odds"])
delete_router = APIRouter(prefix="/api/odds", tags=["odds"])


def _to_read(quote: OddsQuote) -> OddsQuoteRead:
    return OddsQuoteRead(
        id=quote.id,
        match_id=quote.match_id,
        bookmaker_name=quote.bookmaker.name,
        market_type=quote.market_type,
        selection=quote.selection,
        line_value=quote.line_value,
        price_decimal=quote.price_decimal,
        recorded_at=quote.recorded_at,
        source=quote.source,
        is_closing_line=quote.is_closing_line,
    )


def _get_match_or_404(db: Session, match_id: int) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


def _validate_selection_against_match(payload: OddsQuoteCreate, match: Match) -> None:
    if payload.market_type in (MarketType.H2H, MarketType.LINE):
        valid = {match.home_team.name, match.away_team.name}
        if payload.selection not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"selection must be one of {sorted(valid)} for market_type={payload.market_type.value!r}, got {payload.selection!r}",
            )


def _get_or_create_bookmaker(db: Session, name: str) -> Bookmaker:
    bookmaker = db.scalar(select(Bookmaker).where(Bookmaker.name == name))
    if bookmaker is None:
        bookmaker = Bookmaker(name=name)
        db.add(bookmaker)
        db.flush()
    return bookmaker


@odds_router.get("", response_model=list[OddsQuoteRead])
def list_odds(match_id: int, db: Session = Depends(get_db)) -> list[OddsQuoteRead]:
    _get_match_or_404(db, match_id)
    quotes = db.scalars(
        select(OddsQuote).where(OddsQuote.match_id == match_id).order_by(OddsQuote.recorded_at.desc())
    ).all()
    return [_to_read(q) for q in quotes]


@odds_router.post("", response_model=OddsQuoteRead, status_code=201)
def create_odds(match_id: int, payload: OddsQuoteCreate, db: Session = Depends(get_db)) -> OddsQuoteRead:
    match = _get_match_or_404(db, match_id)
    _validate_selection_against_match(payload, match)

    bookmaker = _get_or_create_bookmaker(db, payload.bookmaker_name)
    quote = OddsQuote(
        match_id=match_id,
        bookmaker_id=bookmaker.id,
        market_type=payload.market_type.value,
        selection=payload.selection,
        line_value=payload.line_value,
        price_decimal=payload.price_decimal,
        recorded_at=payload.recorded_at or datetime.now(timezone.utc),
        source="manual",
        is_closing_line=payload.is_closing_line,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return _to_read(quote)


@delete_router.delete("/{odds_id}", status_code=204)
def delete_odds(odds_id: int, db: Session = Depends(get_db)) -> None:
    quote = db.get(OddsQuote, odds_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="Odds quote not found")
    db.delete(quote)
    db.commit()


@bookmakers_router.get("", response_model=list[str])
def list_bookmakers(db: Session = Depends(get_db)) -> list[str]:
    return list(db.scalars(select(Bookmaker.name).order_by(Bookmaker.name)).all())


def _bookmaker_read(b: Bookmaker) -> BookmakerRead:
    return BookmakerRead(id=b.id, name=b.name, provider_key=b.provider_key, region=b.region, is_exchange=b.is_exchange, eligibility=b.eligibility)


@bookmakers_router.get("/eligibility", response_model=list[BookmakerRead])
def list_bookmaker_eligibility(db: Session = Depends(get_db)) -> list[BookmakerRead]:
    """Market Integrity stage, Sections 5 & 13: the settings panel's data
    source — every observed bookmaker, its automatically-detected
    exchange status, and its current (user-editable) eligibility for
    best-price calculations. Never hardcoded; reflects whatever bookmakers
    have actually been seen via manual entry or an automated provider."""
    rows = db.scalars(select(Bookmaker).order_by(Bookmaker.name)).all()
    return [_bookmaker_read(b) for b in rows]


@bookmakers_router.patch("/{bookmaker_id}/eligibility", response_model=BookmakerRead)
def update_bookmaker_eligibility(bookmaker_id: int, payload: BookmakerEligibilityUpdate, db: Session = Depends(get_db)) -> BookmakerRead:
    if payload.eligibility not in VALID_ELIGIBILITIES:
        raise HTTPException(status_code=400, detail=f"eligibility must be one of {sorted(VALID_ELIGIBILITIES)}")
    try:
        bookmaker = set_bookmaker_eligibility(db, bookmaker_id, payload.eligibility)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _bookmaker_read(bookmaker)
