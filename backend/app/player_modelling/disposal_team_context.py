"""Builds the leakage-safe team-context lookup DisposalFeatureBuilder needs
(pre-match Elo win probability, Poisson expected scores/margin) by directly
reusing the EXISTING walk-forward Elo/Poisson replays (app/modelling/
elo_backtest.py, poisson_backtest.py) - the same code and the same
prediction-before-result discipline the live team models already use. Not
reimplemented here: running the real replay is both safer (one leakage-safe
implementation, not two) and cheaper than re-deriving team strength from
scratch for player features.
"""

from sqlalchemy.orm import Session

from app.modelling.data_loading import load_completed_matches
from app.modelling.elo import EloConfig
from app.modelling.elo_backtest import run_walk_forward as elo_walk_forward
from app.modelling.poisson_backtest import run_walk_forward as poisson_walk_forward
from app.modelling.poisson_model import PoissonConfig


def build_team_context(db: Session, sport_code: str = "AFL") -> dict[int, dict[int, dict]]:
    """Returns {match_id: {team_id: {"elo_win_prob", "expected_score",
    "opponent_expected_score", "expected_margin"}}} for every completed
    match with both fixture and goals/behinds data - both walk-forward
    replays are run exactly once, defaults matching what's currently live
    (see app/modelling/elo_cli.py / poisson_cli.py)."""
    matches = load_completed_matches(db, sport_code)

    elo_predictions = elo_walk_forward(matches, EloConfig())
    poisson_predictions = poisson_walk_forward(matches, PoissonConfig())

    context: dict[int, dict[int, dict]] = {}
    for p in elo_predictions:
        context.setdefault(p.match_id, {})
        context[p.match_id][p.home_team_id] = {"elo_win_prob": p.home_win_probability}
        context[p.match_id][p.away_team_id] = {"elo_win_prob": 1.0 - p.home_win_probability}

    for p in poisson_predictions:
        context.setdefault(p.match_id, {})
        home_expected = 6 * p.home_expected_goals + p.home_expected_behinds
        away_expected = 6 * p.away_expected_goals + p.away_expected_behinds
        margin = home_expected - away_expected
        context[p.match_id].setdefault(p.home_team_id, {})
        context[p.match_id].setdefault(p.away_team_id, {})
        context[p.match_id][p.home_team_id].update(
            {"expected_score": home_expected, "opponent_expected_score": away_expected, "expected_margin": margin}
        )
        context[p.match_id][p.away_team_id].update(
            {"expected_score": away_expected, "opponent_expected_score": home_expected, "expected_margin": -margin}
        )

    return context
