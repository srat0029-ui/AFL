"""Bookmaker eligibility + exchange classification (Market Integrity + Final
Weekly Picks stage, Sections 4-5, 13). Single source of truth for two
related questions this stage's real +78.9% "Richmond to win" case exposed:

1. Is this bookmaker a betting EXCHANGE (a back price set by other
   bettors) rather than a fixed-odds sportsbook? Exchange back prices are
   a genuinely different price product - they can legitimately diverge
   much further from sportsbook consensus, especially for illiquid
   longshots, without that being a data bug. Classified from the
   provider's OWN key, not inferred from price behaviour: The Odds API
   returns Betfair's exchange product under provider_key "betfair_ex_au" -
   the only observed key containing "_ex_" - while every plain sportsbook
   key ("sportsbet", "tab", "ladbrokes_au", ...) does not. This mirrors
   the already-correct handling of the separate "h2h_lay" market key
   (Betfair's LAY price, excluded entirely in team_odds_ingestion.py's
   MARKET_KEY_MAP) - this module handles the exchange BACK price, which
   the market-key filter does not touch.

2. Is this bookmaker ELIGIBLE for the user's "best price" calculations?
   included | excluded | informational_only, stored per-bookmaker on
   Bookmaker.eligibility and user-editable via PATCH /api/bookmakers/{id}
   (never hardcoded - Section 5: "Do not hardcode preferences yet").
   Exchanges default to informational_only so an exchange back price can
   never silently become "the" best price compared against sportsbooks
   without disclosure (Section 4); all other bookmakers default to
   included.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bookmaker
from app.models.bookmaker import ELIGIBILITY_EXCLUDED, ELIGIBILITY_INCLUDED, ELIGIBILITY_INFORMATIONAL

# See module docstring: the only pattern observed in real provider data to
# date. A small, explicit, auditable rule rather than a heuristic inferred
# from price divergence - kept as a tuple (not a single constant) so a
# future second exchange provider key pattern can be added transparently.
EXCHANGE_PROVIDER_KEY_MARKERS: tuple[str, ...] = ("_ex_",)

VALID_ELIGIBILITIES = {ELIGIBILITY_INCLUDED, ELIGIBILITY_EXCLUDED, ELIGIBILITY_INFORMATIONAL}


def is_exchange_provider_key(provider_key: str | None) -> bool:
    if provider_key is None:
        return False
    return any(marker in provider_key for marker in EXCHANGE_PROVIDER_KEY_MARKERS)


def default_eligibility(is_exchange: bool) -> str:
    return ELIGIBILITY_INFORMATIONAL if is_exchange else ELIGIBILITY_INCLUDED


def classify_provider_key(provider_key: str | None) -> tuple[bool, str]:
    """Returns (is_exchange, default_eligibility) for a freshly-seen
    bookmaker's provider key - used once, at get-or-create time."""
    exchange = is_exchange_provider_key(provider_key)
    return exchange, default_eligibility(exchange)


@dataclass(frozen=True)
class BookmakerInfo:
    name: str
    provider_key: str | None
    is_exchange: bool
    eligibility: str


def load_bookmaker_info(db: Session) -> dict[str, BookmakerInfo]:
    """Name -> classification, for annotating any `bookmakers` price list
    without a per-call join."""
    rows = db.scalars(select(Bookmaker)).all()
    return {
        b.name: BookmakerInfo(name=b.name, provider_key=b.provider_key, is_exchange=b.is_exchange, eligibility=b.eligibility)
        for b in rows
    }


def annotate_price_entries(bookmakers: list[dict], info_by_name: dict[str, BookmakerInfo]) -> list[dict]:
    """Attaches is_exchange/eligibility to each per-bookmaker price dict
    (the `bookmakers` list already present on every opportunity/insight).
    A bookmaker with no info row (shouldn't happen - every quote is
    persisted with a real Bookmaker FK) is treated as included/non-exchange
    rather than silently dropped."""
    annotated = []
    for entry in bookmakers:
        info = info_by_name.get(entry["bookmaker_name"])
        annotated.append(
            {
                **entry,
                "is_exchange": info.is_exchange if info else False,
                "eligibility": info.eligibility if info else ELIGIBILITY_INCLUDED,
            }
        )
    return annotated


def eligible_entries(bookmakers: list[dict]) -> list[dict]:
    """`bookmakers` must already be annotated (see annotate_price_entries)."""
    return [b for b in bookmakers if b["eligibility"] == ELIGIBILITY_INCLUDED]


def best_price_entry(bookmakers: list[dict]) -> dict | None:
    if not bookmakers:
        return None
    return max(bookmakers, key=lambda b: b["price_decimal"])


def best_prices(bookmakers: list[dict]) -> dict:
    """Section 13: 'Best price among enabled bookmakers' vs 'Best price
    across all observed bookmakers' - always both computed, so the UI can
    show the difference rather than silently picking one. Section 14 adds
    the next-best and worst ENABLED prices - the price-shopping "savings"
    story ("best $X at A / next-best $Y at B / worst $Z at C"), kept
    entirely separate from model edge (which compares model to market, not
    bookmaker to bookmaker). `bookmakers` must already be annotated with
    is_exchange/eligibility."""
    eligible = eligible_entries(bookmakers)
    ranked_eligible = sorted(eligible, key=lambda b: b["price_decimal"], reverse=True)
    best_enabled = ranked_eligible[0] if ranked_eligible else None
    next_best_enabled = ranked_eligible[1] if len(ranked_eligible) >= 2 else None
    worst_enabled = ranked_eligible[-1] if len(ranked_eligible) >= 2 else None
    best_all = best_price_entry(bookmakers)
    differs = (
        best_enabled is not None
        and best_all is not None
        and (best_enabled["bookmaker_name"] != best_all["bookmaker_name"] or best_enabled["price_decimal"] != best_all["price_decimal"])
    )
    return {
        "best_enabled": best_enabled,
        "next_best_enabled": next_best_enabled,
        "worst_enabled": worst_enabled,
        "best_all": best_all,
        "best_all_differs_from_enabled": differs,
    }


def set_bookmaker_eligibility(db: Session, bookmaker_id: int, eligibility: str) -> Bookmaker:
    if eligibility not in VALID_ELIGIBILITIES:
        raise ValueError(f"eligibility must be one of {sorted(VALID_ELIGIBILITIES)}, got {eligibility!r}")
    bookmaker = db.get(Bookmaker, bookmaker_id)
    if bookmaker is None:
        raise ValueError(f"no bookmaker with id={bookmaker_id}")
    bookmaker.eligibility = eligibility
    db.commit()
    db.refresh(bookmaker)
    return bookmaker
