"""Sanity checks for live projections before they're treated as usable —
Section 14 of the team-selection stage brief. Pure functions over
already-computed results (live_engine.LiveProjectionRun); flags anomalies
rather than silently accepting or dropping them — reporting anomalies is
the caller's job (see cli.py's project-upcoming/refresh-live commands).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.player_modelling.disposal_distribution import NegativeBinomialDistribution
from app.player_modelling.goal_distribution import HurdleDistribution, NegativeBinomialGoalDistribution
from app.player_modelling.live_engine import DisposalProjectionResult, GoalProjectionResult, LiveProjectionRun

# Generous plausible-range bounds. The real audited dataset's observed
# maxima are 54 disposals / 11 goals in a single game (see
# disposal_distribution.MAX_DISPOSALS / goal_distribution.MAX_GOALS) — these
# thresholds are deliberately well inside those hard caps, since an
# EXPECTED value anywhere near the historical single-game maximum would
# itself be a red flag, not a plausible mean.
DISPOSAL_PLAUSIBLE_RANGE = (0.0, 45.0)
GOAL_PLAUSIBLE_RANGE = (0.0, 6.0)

DISPOSAL_SANITY_THRESHOLDS = (15, 20, 25, 30, 35, 40)
GOAL_SANITY_THRESHOLDS = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class SanityAnomaly:
    category: str
    player_id: int
    match_id: int
    detail: str


def check_disposal_projection(p: DisposalProjectionResult) -> list[SanityAnomaly]:
    anomalies: list[SanityAnomaly] = []
    lo, hi = DISPOSAL_PLAUSIBLE_RANGE
    if not (lo <= p.predicted_mean <= hi):
        anomalies.append(
            SanityAnomaly(
                "implausible_mean", p.player_id, p.match_id,
                f"expected disposals {p.predicted_mean:.1f} outside plausible range [{lo:.0f}, {hi:.0f}]",
            )
        )

    dist = NegativeBinomialDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)
    probs = [dist.prob_at_least(t) for t in DISPOSAL_SANITY_THRESHOLDS]
    if probs != sorted(probs, reverse=True):
        anomalies.append(
            SanityAnomaly("non_monotonic_probabilities", p.player_id, p.match_id, f"threshold probabilities not monotonically decreasing: {probs}")
        )

    lo50, hi50 = dist.interval(0.5)
    lo80, hi80 = dist.interval(0.8)
    lo90, hi90 = dist.interval(0.9)
    if not (lo50 <= hi50 and lo80 <= hi80 and lo90 <= hi90):
        anomalies.append(SanityAnomaly("interval_ordering", p.player_id, p.match_id, "an interval's lower bound exceeds its own upper bound"))
    if not (lo90 <= lo80 <= lo50 <= hi50 <= hi80 <= hi90):
        anomalies.append(
            SanityAnomaly(
                "interval_nesting", p.player_id, p.match_id,
                f"intervals not nested as expected: 50%={(lo50, hi50)} 80%={(lo80, hi80)} 90%={(lo90, hi90)}",
            )
        )

    return anomalies


def check_goal_projection(p: GoalProjectionResult) -> list[SanityAnomaly]:
    anomalies: list[SanityAnomaly] = []
    lo, hi = GOAL_PLAUSIBLE_RANGE
    if not (lo <= p.predicted_mean <= hi):
        anomalies.append(
            SanityAnomaly("implausible_mean", p.player_id, p.match_id, f"expected goals {p.predicted_mean:.2f} outside plausible range [{lo:.0f}, {hi:.0f}]")
        )

    if p.distribution_kind == "hurdle":
        dist = HurdleDistribution(p_score=p.p_score, mu_scored=p.mu_scored, alpha_scored=p.alpha_scored)
    else:
        dist = NegativeBinomialGoalDistribution(mu=p.predicted_mean, alpha=p.nb_alpha)
    probs = [dist.prob_at_least(t) for t in GOAL_SANITY_THRESHOLDS]
    if probs != sorted(probs, reverse=True):
        anomalies.append(
            SanityAnomaly("non_monotonic_probabilities", p.player_id, p.match_id, f"threshold probabilities not monotonically decreasing: {probs}")
        )

    return anomalies


def check_no_projection_for_confirmed_out(
    run: LiveProjectionRun, confirmed_out_player_ids_by_match: dict[int, set[int]]
) -> list[SanityAnomaly]:
    """Defense-in-depth: upcoming_features.load_expected_players already
    structurally excludes confirmed-out players from ever being projected
    (only expected_in/uncertain are returned), so this should never fire in
    practice - it exists to actually VERIFY that guarantee holds, per
    Section 14's explicit requirement, rather than just trust it silently."""
    anomalies: list[SanityAnomaly] = []
    for p in run.disposal_projections:
        if p.player_id in confirmed_out_player_ids_by_match.get(p.match_id, set()):
            anomalies.append(SanityAnomaly("projection_for_confirmed_out", p.player_id, p.match_id, "a disposal projection exists for a player marked confirmed_out"))
    for p in run.goal_projections:
        if p.player_id in confirmed_out_player_ids_by_match.get(p.match_id, set()):
            anomalies.append(SanityAnomaly("projection_for_confirmed_out", p.player_id, p.match_id, "a goal projection exists for a player marked confirmed_out"))
    return anomalies


def confirmed_out_player_ids_by_match(db: Session, match_ids: list[int]) -> dict[int, set[int]]:
    """Shared by every caller of run_all_sanity_checks (project-upcoming,
    refresh-live, run-live-cycle) so this query is written once."""
    from app.models import ExpectedLineup, SelectionStatus

    if not match_ids:
        return {}
    rows = db.scalars(
        select(ExpectedLineup).where(ExpectedLineup.match_id.in_(match_ids), ExpectedLineup.selection_status == SelectionStatus.CONFIRMED_OUT.value)
    ).all()
    result: dict[int, set[int]] = {}
    for r in rows:
        result.setdefault(r.match_id, set()).add(r.player_id)
    return result


def run_all_sanity_checks(
    run: LiveProjectionRun, confirmed_out_player_ids_by_match: dict[int, set[int]] | None = None
) -> list[SanityAnomaly]:
    anomalies: list[SanityAnomaly] = []
    for p in run.disposal_projections:
        anomalies.extend(check_disposal_projection(p))
    for p in run.goal_projections:
        anomalies.extend(check_goal_projection(p))
    if confirmed_out_player_ids_by_match:
        anomalies.extend(check_no_projection_for_confirmed_out(run, confirmed_out_player_ids_by_match))
    return anomalies
