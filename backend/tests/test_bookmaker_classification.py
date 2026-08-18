"""Tests for bookmaker exchange classification + eligibility (Market
Integrity stage, Sections 4-5, 13)."""

from sqlalchemy import select

from app.models import Bookmaker
from app.models.bookmaker import ELIGIBILITY_EXCLUDED, ELIGIBILITY_INCLUDED, ELIGIBILITY_INFORMATIONAL
from app.player_modelling.bookmaker_classification import (
    annotate_price_entries,
    best_prices,
    classify_provider_key,
    is_exchange_provider_key,
    load_bookmaker_info,
    set_bookmaker_eligibility,
)


def test_betfair_exchange_key_detected():
    assert is_exchange_provider_key("betfair_ex_au") is True


def test_plain_sportsbook_keys_not_exchange():
    for key in ("sportsbet", "tab", "ladbrokes_au", "betr_au", "pointsbetau", None):
        assert is_exchange_provider_key(key) is False


def test_classify_provider_key_defaults():
    is_exchange, eligibility = classify_provider_key("betfair_ex_au")
    assert is_exchange is True
    assert eligibility == ELIGIBILITY_INFORMATIONAL

    is_exchange, eligibility = classify_provider_key("sportsbet")
    assert is_exchange is False
    assert eligibility == ELIGIBILITY_INCLUDED


def test_annotate_price_entries_uses_bookmaker_info(db_session):
    db_session.add_all([
        Bookmaker(name="TAB", provider_key="tab", is_exchange=False, eligibility=ELIGIBILITY_INCLUDED),
        Bookmaker(name="Betfair", provider_key="betfair_ex_au", is_exchange=True, eligibility=ELIGIBILITY_INFORMATIONAL),
    ])
    db_session.commit()
    info = load_bookmaker_info(db_session)

    entries = [
        {"bookmaker_name": "TAB", "price_decimal": 2.0},
        {"bookmaker_name": "Betfair", "price_decimal": 5.0},
        {"bookmaker_name": "Unknown Book", "price_decimal": 3.0},
    ]
    annotated = annotate_price_entries(entries, info)
    by_name = {e["bookmaker_name"]: e for e in annotated}
    assert by_name["TAB"]["is_exchange"] is False
    assert by_name["TAB"]["eligibility"] == ELIGIBILITY_INCLUDED
    assert by_name["Betfair"]["is_exchange"] is True
    assert by_name["Betfair"]["eligibility"] == ELIGIBILITY_INFORMATIONAL
    # An unresolved bookmaker (shouldn't normally happen) is treated as
    # eligible/non-exchange rather than silently dropped.
    assert by_name["Unknown Book"]["is_exchange"] is False
    assert by_name["Unknown Book"]["eligibility"] == ELIGIBILITY_INCLUDED


def test_best_prices_excludes_informational_bookmaker_from_best_enabled():
    bookmakers = [
        {"bookmaker_name": "Betfair", "price_decimal": 34.0, "eligibility": ELIGIBILITY_INFORMATIONAL, "is_exchange": True},
        {"bookmaker_name": "PointsBet", "price_decimal": 19.0, "eligibility": ELIGIBILITY_INCLUDED, "is_exchange": False},
        {"bookmaker_name": "TAB", "price_decimal": 18.0, "eligibility": ELIGIBILITY_INCLUDED, "is_exchange": False},
    ]
    summary = best_prices(bookmakers)
    assert summary["best_enabled"]["bookmaker_name"] == "PointsBet"
    assert summary["next_best_enabled"]["bookmaker_name"] == "TAB"
    assert summary["worst_enabled"]["bookmaker_name"] == "TAB"
    assert summary["best_all"]["bookmaker_name"] == "Betfair"
    assert summary["best_all_differs_from_enabled"] is True


def test_best_prices_no_difference_when_best_is_already_eligible():
    bookmakers = [
        {"bookmaker_name": "TAB", "price_decimal": 2.5, "eligibility": ELIGIBILITY_INCLUDED, "is_exchange": False},
        {"bookmaker_name": "SportsBet", "price_decimal": 2.4, "eligibility": ELIGIBILITY_INCLUDED, "is_exchange": False},
    ]
    summary = best_prices(bookmakers)
    assert summary["best_all_differs_from_enabled"] is False
    assert summary["best_enabled"]["bookmaker_name"] == "TAB"


def test_best_prices_next_and_worst_none_with_single_eligible_bookmaker():
    bookmakers = [{"bookmaker_name": "TAB", "price_decimal": 2.5, "eligibility": ELIGIBILITY_INCLUDED, "is_exchange": False}]
    summary = best_prices(bookmakers)
    assert summary["best_enabled"]["bookmaker_name"] == "TAB"
    assert summary["next_best_enabled"] is None
    assert summary["worst_enabled"] is None


def test_set_bookmaker_eligibility_updates_and_persists(db_session):
    bookmaker = Bookmaker(name="TAB", provider_key="tab", is_exchange=False, eligibility=ELIGIBILITY_INCLUDED)
    db_session.add(bookmaker)
    db_session.commit()

    updated = set_bookmaker_eligibility(db_session, bookmaker.id, ELIGIBILITY_EXCLUDED)
    assert updated.eligibility == ELIGIBILITY_EXCLUDED

    reloaded = db_session.scalar(select(Bookmaker).where(Bookmaker.id == bookmaker.id))
    assert reloaded.eligibility == ELIGIBILITY_EXCLUDED


def test_set_bookmaker_eligibility_rejects_invalid_value(db_session):
    bookmaker = Bookmaker(name="TAB", provider_key="tab")
    db_session.add(bookmaker)
    db_session.commit()
    try:
        set_bookmaker_eligibility(db_session, bookmaker.id, "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_set_bookmaker_eligibility_missing_bookmaker_raises(db_session):
    try:
        set_bookmaker_eligibility(db_session, 999999, ELIGIBILITY_INCLUDED)
        assert False, "expected ValueError"
    except ValueError:
        pass
