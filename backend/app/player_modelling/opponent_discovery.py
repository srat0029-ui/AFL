"""Opponent discovery for Player Research - given a player and a stat,
finds which opponent teams they have actually faced with enough recorded
history to be worth investigating with the detailed opponent-context
comparison (see opponent_context_analysis.py, whose exact split/
adjustment/confidence methodology this module reuses UNCHANGED for every
candidate).

This answers "which opponents are actually worth investigating for this
player" (an evidence-triage question), not "which opponent do they
perform best against" - see build_opponent_discovery's docstring for the
exact ordering, which is keyed on evidence sufficiency and sample size,
never on the size of a raw or adjusted difference. A single noisy
small-sample outlier can never out-rank a well-evidenced opponent.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.match import Match
from app.models.player import Player
from app.models.player_match_stat import PlayerMatchStat
from app.models.team import Team
from app.player_modelling.opponent_context_analysis import (
    _opponent_adjusted_effect,
    _opponent_confidence,
)
from app.player_modelling.player_context_analysis import (
    DEFAULT_THRESHOLDS,
    STAT_FIELDS,
    SUPPORTED_STATS,
    AdjustedTeammateEffect,
    ContextSplitStats,
    PlayerContextConfidence,
    PlayerContextConfidenceTier,
    _split_stats,
    compute_trailing_baselines,
)

__all__ = [
    "SUPPORTED_STATS",
    "OpponentCandidate",
    "OpponentDiscovery",
    "build_opponent_discovery",
    "opponent_discovery_as_dict",
]

_CONFIDENCE_RANK: dict[PlayerContextConfidenceTier, int] = {
    PlayerContextConfidenceTier.INSUFFICIENT_HISTORY: 0,
    PlayerContextConfidenceTier.LOWER: 1,
    PlayerContextConfidenceTier.MODERATE: 2,
    PlayerContextConfidenceTier.HIGHER: 3,
}


@dataclass(frozen=True)
class OpponentCandidate:
    opponent_team_id: int
    opponent_team_name: str
    against_opponent: ContextSplitStats
    against_other_opponents: ContextSplitStats
    raw_difference: float | None
    adjusted_effect: AdjustedTeammateEffect
    confidence: PlayerContextConfidence
    sufficient_evidence: bool


@dataclass(frozen=True)
class OpponentDiscovery:
    player_id: int
    player_name: str
    team_id: int | None
    team_name: str | None
    stat: str
    thresholds: tuple[int, ...]
    candidates: list[OpponentCandidate]
    explanation: str
    scope_explanation: str


def _candidate_sort_key(candidate: OpponentCandidate) -> tuple[int, int, int, str]:
    # Sufficient evidence first; within that, higher confidence tier, then
    # more games against that opponent. Deliberately NEVER keyed on
    # raw_difference or adjusted_effect magnitude - see module docstring.
    return (
        0 if candidate.sufficient_evidence else 1,
        -_CONFIDENCE_RANK[candidate.confidence.tier],
        -candidate.against_opponent.games,
        candidate.opponent_team_name,
    )


def build_opponent_discovery(
    db: Session,
    player_id: int,
    stat: str = "disposals",
    thresholds: Sequence[int] | None = None,
) -> OpponentDiscovery:
    if stat not in STAT_FIELDS:
        raise ValueError(f"Unsupported stat {stat!r} - must be one of {sorted(SUPPORTED_STATS)}")
    stat_field = STAT_FIELDS[stat]
    resolved_thresholds: tuple[int, ...] = tuple(thresholds) if thresholds else DEFAULT_THRESHOLDS[stat]

    scope_explanation = (
        "This history is restricted to the player's most recent recorded club "
        "(per PlayerMatchStat.team_id, the source of truth for who played for whom on a given date - "
        "not the convenience players.current_team_id field)."
    )

    player = db.get(Player, player_id)
    if player is None:
        raise LookupError(f"Player {player_id} not found")

    player_rows: list[PlayerMatchStat] = list(
        db.scalars(
            select(PlayerMatchStat)
            .join(Match, PlayerMatchStat.match_id == Match.id)
            .where(PlayerMatchStat.player_id == player_id)
            .order_by(Match.scheduled_start.asc(), PlayerMatchStat.match_id.asc())
        ).all()
    )

    if not player_rows:
        return OpponentDiscovery(
            player_id=player_id,
            player_name=player.display_name,
            team_id=None,
            team_name=None,
            stat=stat,
            thresholds=resolved_thresholds,
            candidates=[],
            explanation="No recorded match statistics found for this player.",
            scope_explanation=scope_explanation,
        )

    # Same convention as player_context_analysis / teammate_discovery /
    # opponent_context_analysis.
    team_id = player_rows[-1].team_id
    team = db.get(Team, team_id)
    club_rows = [r for r in player_rows if r.team_id == team_id]
    match_ids = [r.match_id for r in club_rows]

    matches_by_id = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()}
    ordered = sorted(club_rows, key=lambda r: (matches_by_id[r.match_id].scheduled_start, r.match_id))
    # Baselines computed over the FULL club history, including any
    # opponent-unknown games - see opponent_context_analysis's docstring.
    baselines = compute_trailing_baselines(ordered, stat_field)

    known_opponent_rows = [r for r in ordered if r.opponent_team_id is not None]
    if not known_opponent_rows:
        return OpponentDiscovery(
            player_id=player_id,
            player_name=player.display_name,
            team_id=team_id,
            team_name=team.name if team else None,
            stat=stat,
            thresholds=resolved_thresholds,
            candidates=[],
            explanation=(
                f"No recorded opponent information exists for {team.name if team else 'this club'} in "
                "this player's matches there - no opponent comparison is possible."
            ),
            scope_explanation=scope_explanation,
        )

    match_ids_by_opponent: dict[int, set[int]] = defaultdict(set)
    for r in known_opponent_rows:
        match_ids_by_opponent[r.opponent_team_id].add(r.match_id)

    opponent_teams_by_id = {
        t.id: t for t in db.scalars(select(Team).where(Team.id.in_(match_ids_by_opponent.keys()))).all()
    }

    candidates: list[OpponentCandidate] = []
    for opponent_team_id, opponent_match_ids in match_ids_by_opponent.items():
        opponent_team = opponent_teams_by_id.get(opponent_team_id)
        if opponent_team is None:
            continue
        against_rows = [r for r in known_opponent_rows if r.match_id in opponent_match_ids]
        other_rows = [r for r in known_opponent_rows if r.match_id not in opponent_match_ids]
        against_stats = _split_stats(against_rows, stat_field, resolved_thresholds)
        other_stats = _split_stats(other_rows, stat_field, resolved_thresholds)
        raw_difference = (
            against_stats.mean - other_stats.mean
            if against_stats.mean is not None and other_stats.mean is not None
            else None
        )
        adjusted = _opponent_adjusted_effect(known_opponent_rows, baselines, opponent_match_ids, stat_field)
        confidence = _opponent_confidence(against_stats.games, other_stats.games)
        candidates.append(
            OpponentCandidate(
                opponent_team_id=opponent_team_id,
                opponent_team_name=opponent_team.name,
                against_opponent=against_stats,
                against_other_opponents=other_stats,
                raw_difference=raw_difference,
                adjusted_effect=adjusted,
                confidence=confidence,
                sufficient_evidence=confidence.tier != PlayerContextConfidenceTier.INSUFFICIENT_HISTORY,
            )
        )

    candidates.sort(key=_candidate_sort_key)

    n_sufficient = sum(1 for c in candidates if c.sufficient_evidence)
    return OpponentDiscovery(
        player_id=player_id,
        player_name=player.display_name,
        team_id=team_id,
        team_name=team.name if team else None,
        stat=stat,
        thresholds=resolved_thresholds,
        candidates=candidates,
        explanation=(
            f"{len(candidates)} opponent(s) found with at least one recorded game against them for "
            f"{team.name if team else 'this club'}, {n_sufficient} with enough games in both groups to "
            "treat as more than anecdotal. Ordered by evidence sufficiency, then confidence tier, then "
            "sample size against that opponent - never by the size of a statistical difference."
        ),
        scope_explanation=scope_explanation,
    )


def _split_as_dict(s: ContextSplitStats) -> dict:
    return {
        "games": s.games,
        "stat_sample_size": s.stat_sample_size,
        "mean": s.mean,
        "median": s.median,
        "milestone_rates": {f"{t}+": v for t, v in s.milestone_rates.items()},
        "average_time_on_ground_pct": s.average_time_on_ground_pct,
        "time_on_ground_sample_size": s.time_on_ground_sample_size,
    }


def opponent_discovery_as_dict(discovery: OpponentDiscovery) -> dict:
    return {
        "player_id": discovery.player_id,
        "player_name": discovery.player_name,
        "team_id": discovery.team_id,
        "team_name": discovery.team_name,
        "stat": discovery.stat,
        "thresholds": list(discovery.thresholds),
        "explanation": discovery.explanation,
        "scope_explanation": discovery.scope_explanation,
        "candidates": [
            {
                "opponent_team_id": c.opponent_team_id,
                "opponent_team_name": c.opponent_team_name,
                "against_opponent": _split_as_dict(c.against_opponent),
                "against_other_opponents": _split_as_dict(c.against_other_opponents),
                "raw_difference": c.raw_difference,
                "adjusted_effect": {
                    "available": c.adjusted_effect.available,
                    "value": c.adjusted_effect.value,
                    "games_with_baseline_against_opponent": c.adjusted_effect.games_with_baseline_teammate_in,
                    "games_with_baseline_other_opponents": c.adjusted_effect.games_with_baseline_teammate_out,
                    "method": c.adjusted_effect.method,
                    "explanation": c.adjusted_effect.explanation,
                },
                "confidence": {"tier": c.confidence.tier.value, "warnings": c.confidence.warnings},
                "sufficient_evidence": c.sufficient_evidence,
            }
            for c in discovery.candidates
        ],
    }
