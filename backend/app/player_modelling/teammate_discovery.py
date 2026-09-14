"""Teammate discovery for Player Research - given a player and a stat,
finds which teammates have enough shared match history to be worth
investigating with the existing with/without-teammate context analysis
(see player_context_analysis.py, whose exact split/adjustment/confidence
methodology this module reuses UNCHANGED for every candidate - this is
deliberate: an adjusted effect here is only ever the same
point-in-time-safe, sample-size-gated estimate the single-teammate
endpoint would compute for that same pair, never a new or looser
calculation).

This answers "who is worth investigating" (an evidence-triage question),
not "who has the biggest effect" - see build_teammate_discovery's
docstring for the exact ordering, which is keyed on evidence sufficiency
and shared-match sample size, never on the size of a raw or adjusted
difference. A single noisy small-sample outlier can never out-rank a
well-evidenced teammate.
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
from app.player_modelling.player_context_analysis import (
    DEFAULT_THRESHOLDS,
    STAT_FIELDS,
    SUPPORTED_STATS,
    AdjustedTeammateEffect,
    ContextSplitStats,
    PlayerContextConfidence,
    PlayerContextConfidenceTier,
    _adjusted_effect,
    _confidence,
    _split_stats,
    compute_trailing_baselines,
)

__all__ = [
    "SUPPORTED_STATS",
    "TeammateCandidate",
    "TeammateDiscovery",
    "build_teammate_discovery",
    "teammate_discovery_as_dict",
]

_CONFIDENCE_RANK: dict[PlayerContextConfidenceTier, int] = {
    PlayerContextConfidenceTier.INSUFFICIENT_HISTORY: 0,
    PlayerContextConfidenceTier.LOWER: 1,
    PlayerContextConfidenceTier.MODERATE: 2,
    PlayerContextConfidenceTier.HIGHER: 3,
}


@dataclass(frozen=True)
class TeammateCandidate:
    teammate_id: int
    teammate_name: str
    with_teammate: ContextSplitStats
    without_teammate: ContextSplitStats
    raw_difference: float | None
    adjusted_effect: AdjustedTeammateEffect
    confidence: PlayerContextConfidence
    sufficient_evidence: bool


@dataclass(frozen=True)
class TeammateDiscovery:
    player_id: int
    player_name: str
    team_id: int | None
    team_name: str | None
    stat: str
    thresholds: tuple[int, ...]
    candidates: list[TeammateCandidate]
    explanation: str


def _candidate_sort_key(candidate: TeammateCandidate) -> tuple[int, int, int, str]:
    # Sufficient evidence first; within that, higher confidence tier, then
    # more shared matches. Deliberately NEVER keyed on raw_difference or
    # adjusted_effect magnitude - see module docstring.
    return (
        0 if candidate.sufficient_evidence else 1,
        -_CONFIDENCE_RANK[candidate.confidence.tier],
        -candidate.with_teammate.games,
        candidate.teammate_name,
    )


def build_teammate_discovery(
    db: Session,
    player_id: int,
    stat: str = "disposals",
    thresholds: Sequence[int] | None = None,
) -> TeammateDiscovery:
    if stat not in STAT_FIELDS:
        raise ValueError(f"Unsupported stat {stat!r} - must be one of {sorted(SUPPORTED_STATS)}")
    stat_field = STAT_FIELDS[stat]
    resolved_thresholds: tuple[int, ...] = tuple(thresholds) if thresholds else DEFAULT_THRESHOLDS[stat]

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
        return TeammateDiscovery(
            player_id=player_id,
            player_name=player.display_name,
            team_id=None,
            team_name=None,
            stat=stat,
            thresholds=resolved_thresholds,
            candidates=[],
            explanation="No recorded match statistics found for this player.",
        )

    # Same convention as player_context_analysis: restrict to the player's
    # most recent club, per PlayerMatchStat.team_id (the source of truth
    # for who played for whom on a given date - see that module's
    # docstring for why players.current_team_id is never used here).
    team_id = player_rows[-1].team_id
    team = db.get(Team, team_id)
    club_rows = [r for r in player_rows if r.team_id == team_id]
    match_ids = [r.match_id for r in club_rows]

    matches_by_id = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()}
    ordered = sorted(club_rows, key=lambda r: (matches_by_id[r.match_id].scheduled_start, r.match_id))
    baselines = compute_trailing_baselines(ordered, stat_field)

    # Every OTHER player with a same-team row in one of this player's own
    # matches at this club is, by definition, a real teammate for at least
    # one shared match - this is the candidate pool itself, not a guess.
    teammate_appearance_rows = db.execute(
        select(PlayerMatchStat.player_id, PlayerMatchStat.match_id).where(
            PlayerMatchStat.team_id == team_id,
            PlayerMatchStat.match_id.in_(match_ids),
            PlayerMatchStat.player_id != player_id,
        )
    ).all()
    match_ids_by_teammate: dict[int, set[int]] = defaultdict(set)
    for teammate_id, match_id in teammate_appearance_rows:
        match_ids_by_teammate[teammate_id].add(match_id)

    if not match_ids_by_teammate:
        return TeammateDiscovery(
            player_id=player_id,
            player_name=player.display_name,
            team_id=team_id,
            team_name=team.name if team else None,
            stat=stat,
            thresholds=resolved_thresholds,
            candidates=[],
            explanation=(
                f"No other player has a recorded match statistic for {team.name if team else 'this club'} "
                "in any of this player's matches there - no teammate comparison is possible."
            ),
        )

    teammates_by_id = {
        p.id: p for p in db.scalars(select(Player).where(Player.id.in_(match_ids_by_teammate.keys()))).all()
    }

    candidates: list[TeammateCandidate] = []
    for teammate_id, teammate_match_ids in match_ids_by_teammate.items():
        teammate = teammates_by_id.get(teammate_id)
        if teammate is None:
            continue
        with_rows = [r for r in ordered if r.match_id in teammate_match_ids]
        without_rows = [r for r in ordered if r.match_id not in teammate_match_ids]
        with_stats = _split_stats(with_rows, stat_field, resolved_thresholds)
        without_stats = _split_stats(without_rows, stat_field, resolved_thresholds)
        raw_difference = (
            without_stats.mean - with_stats.mean
            if with_stats.mean is not None and without_stats.mean is not None
            else None
        )
        adjusted = _adjusted_effect(ordered, baselines, teammate_match_ids, stat_field)
        confidence = _confidence(with_stats.games, without_stats.games)
        candidates.append(
            TeammateCandidate(
                teammate_id=teammate_id,
                teammate_name=teammate.display_name,
                with_teammate=with_stats,
                without_teammate=without_stats,
                raw_difference=raw_difference,
                adjusted_effect=adjusted,
                confidence=confidence,
                sufficient_evidence=confidence.tier != PlayerContextConfidenceTier.INSUFFICIENT_HISTORY,
            )
        )

    candidates.sort(key=_candidate_sort_key)

    n_sufficient = sum(1 for c in candidates if c.sufficient_evidence)
    return TeammateDiscovery(
        player_id=player_id,
        player_name=player.display_name,
        team_id=team_id,
        team_name=team.name if team else None,
        stat=stat,
        thresholds=resolved_thresholds,
        candidates=candidates,
        explanation=(
            f"{len(candidates)} teammate(s) found with at least one shared match for "
            f"{team.name if team else 'this club'}, {n_sufficient} with enough games in both groups to "
            "treat as more than anecdotal. Ordered by evidence sufficiency, then confidence tier, then "
            "shared-match sample size - never by the size of a statistical difference."
        ),
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


def teammate_discovery_as_dict(discovery: TeammateDiscovery) -> dict:
    return {
        "player_id": discovery.player_id,
        "player_name": discovery.player_name,
        "team_id": discovery.team_id,
        "team_name": discovery.team_name,
        "stat": discovery.stat,
        "thresholds": list(discovery.thresholds),
        "explanation": discovery.explanation,
        "candidates": [
            {
                "teammate_id": c.teammate_id,
                "teammate_name": c.teammate_name,
                "with_teammate": _split_as_dict(c.with_teammate),
                "without_teammate": _split_as_dict(c.without_teammate),
                "raw_difference": c.raw_difference,
                "adjusted_effect": {
                    "available": c.adjusted_effect.available,
                    "value": c.adjusted_effect.value,
                    "games_with_baseline_teammate_in": c.adjusted_effect.games_with_baseline_teammate_in,
                    "games_with_baseline_teammate_out": c.adjusted_effect.games_with_baseline_teammate_out,
                    "method": c.adjusted_effect.method,
                    "explanation": c.adjusted_effect.explanation,
                },
                "confidence": {"tier": c.confidence.tier.value, "warnings": c.confidence.warnings},
                "sufficient_evidence": c.sufficient_evidence,
            }
            for c in discovery.candidates
        ],
    }
