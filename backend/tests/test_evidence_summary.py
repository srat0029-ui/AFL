"""Tests for the opportunity evidence/caution summary (Weekly Bet Review
stage, Sections 12-13) — must never hide negative evidence."""

from app.player_modelling.context_model_conflict import CTX_MODEL_PREDATES_CONTEXT, CTX_PLAYER_CONFIRMED_OUT, ContextConflictResult
from app.player_modelling.evidence_summary import build_evidence_summary
from app.player_modelling.market_movement import MarketMovement, TOWARD_MODEL, AWAY_FROM_MODEL


def _opportunity(**overrides):
    o = {
        "opportunity_type": "team",
        "market_type": "h2h",
        "difference_pp": 0.10,
        "bookmakers": [
            {"bookmaker_name": "A", "price_decimal": 2.2, "eligibility": "included"},
            {"bookmaker_name": "B", "price_decimal": 2.0, "eligibility": "included"},
        ],
        "confidence_tier": "higher_confidence",
        "calibration": None,
        "is_confirmed": None,
        "warnings": [],
    }
    o.update(overrides)
    return o


def _movement(direction):
    return MarketMovement(
        first_price=2.0, first_observed_at=None, latest_price=2.2, latest_observed_at=None,
        best_current_price=2.2, model_fair_odds=1.9, direction=direction, description="x",
    )


def test_evidence_includes_existing_reason_codes():
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True)
    assert "Model probability is above the market's price" in summary.evidence_labels


def test_market_moved_toward_model_is_evidence():
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True, movement=_movement(TOWARD_MODEL))
    assert any("moved toward" in l for l in summary.evidence_labels)


def test_market_moved_away_from_model_is_caution_not_evidence():
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True, movement=_movement(AWAY_FROM_MODEL))
    assert any("moved away" in l for l in summary.caution_labels)
    assert not any("moved away" in l for l in summary.evidence_labels)


def test_negative_evidence_never_hidden_even_with_strong_headline():
    # A large positive difference AND multiple caution flags simultaneously -
    # the caution flags must still all appear (Section 13).
    summary = build_evidence_summary(
        _opportunity(difference_pp=0.30, is_confirmed=False, opportunity_type="player"),
        any_confirmed_player_lineups=False,
        movement=_movement(AWAY_FROM_MODEL),
        form_disagreement=True,
    )
    assert len(summary.caution_labels) >= 2
    assert any("moved away" in l for l in summary.caution_labels)


def test_caution_and_evidence_are_independent_lists():
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True)
    assert isinstance(summary.evidence_labels, list)
    assert isinstance(summary.caution_labels, list)


def test_context_conflict_codes_appear_as_caution_never_evidence():
    """Current Context + Team News Intelligence stage, Sections 5/12-13:
    context flags (e.g. a player confirmed out, or the model predating
    newer context) must land under caution, and never be spun as support
    for the opportunity."""
    conflict = ContextConflictResult(
        codes=[CTX_PLAYER_CONFIRMED_OUT, CTX_MODEL_PREDATES_CONTEXT],
        labels=["Player confirmed out", "Model may not fully reflect latest context"],
        latest_context_at=None, model_generated_at=None,
    )
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True, context_conflict=conflict)
    assert "Player confirmed out" in summary.caution_labels
    assert "Model may not fully reflect latest context" in summary.caution_labels
    assert "Player confirmed out" not in summary.evidence_labels
    assert "Model may not fully reflect latest context" not in summary.evidence_labels


def test_no_context_conflict_is_a_safe_default():
    summary = build_evidence_summary(_opportunity(), any_confirmed_player_lineups=True, context_conflict=None)
    assert isinstance(summary.caution_labels, list)
