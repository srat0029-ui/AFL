"""Opponent context analysis - "how does Player X perform against Team Y
compared with every other opponent they've faced" and the related
evidence/confidence/adjustment machinery (the Opponent Context Research
direction, alongside the with/without-teammate direction in
player_context_analysis.py).

Reuses player_context_analysis.py's split/trailing-baseline/adjusted-
effect/confidence NUMERIC methodology unchanged - only how the two
comparison groups are defined differs (matches against the selected
opponent vs matches against every other opponent, instead of matches
with/without a teammate). See that module's docstring for the underlying
design principles (never-causal framing, point-in-time-safe baseline,
rule-based confidence tiering) and compute_trailing_baselines's docstring
for the point-in-time-safety guarantee this module depends on.

Team-context resolution: identical convention to player_context_analysis
- restricted to the player's most recent club as evidenced by
PlayerMatchStat.team_id (never players.current_team_id - see that
module's docstring for why). "Opponents faced" is only a coherent
question within one club stint; mixing stints would conflate different
team environments.

Games where PlayerMatchStat.opponent_team_id is NULL (opponent unknown)
are excluded from both comparison groups and from the evidence table -
they are never guessed into "against other opponents", since that would
misrepresent data we don't actually have. They are still allowed to
contribute to the player's trailing-form history (see the baseline
computed over the full ordered club history below), since the player's
own recorded stat value that game is real even if the opponent label is
missing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.match import Match
from app.models.player import Player
from app.models.player_match_stat import PlayerMatchStat
from app.models.round import Round
from app.models.season import Season
from app.models.team import Team
from app.models.venue import Venue
from app.player_modelling.player_context_analysis import (
    DEFAULT_THRESHOLDS,
    DEFAULT_TRAILING_FORM_WINDOW,
    LOWER_CONFIDENCE_GAMES,
    MIN_GAMES_INSUFFICIENT,
    MODERATE_CONFIDENCE_GAMES,
    STAT_FIELDS,
    SUPPORTED_STATS,
    AdjustedTeammateEffect,
    ConfounderNote,
    ContextSplitStats,
    PlayerContextConfidence,
    PlayerContextConfidenceTier,
    _adjusted_effect,
    _empty_split,
    _split_stats,
    _ROLE_UNAVAILABLE_EXPLANATION,
    compute_trailing_baselines,
)

__all__ = [
    "SUPPORTED_STATS",
    "OpponentContextAnalysis",
    "OpponentEvidenceRow",
    "build_opponent_context_analysis",
    "opponent_context_analysis_as_dict",
]


@dataclass(frozen=True)
class OpponentEvidenceRow:
    match_id: int
    season_year: int
    round_number: int
    round_name: str | None
    scheduled_start: datetime
    team_id: int
    team_name: str
    opponent_team_id: int
    opponent_name: str
    venue_name: str | None
    is_home: bool | None
    is_selected_opponent: bool
    stat_value: int | None
    time_on_ground_pct: int | None


@dataclass(frozen=True)
class OpponentContextAnalysis:
    player_id: int
    player_name: str
    opponent_team_id: int
    opponent_team_name: str
    team_id: int | None
    team_name: str | None
    stat: str
    thresholds: tuple[int, ...]
    against_opponent: ContextSplitStats
    against_other_opponents: ContextSplitStats
    # against_opponent.mean - against_other_opponents.mean - positive means
    # a HIGHER historical average against this opponent than against the
    # rest of the fixture list. Never presented as causal - see adjusted_effect.
    raw_difference: float | None
    adjusted_effect: AdjustedTeammateEffect
    confounders: dict[str, ConfounderNote]
    confidence: PlayerContextConfidence
    evidence: list[OpponentEvidenceRow]
    role_analysis_available: bool
    role_analysis_explanation: str
    scope_explanation: str


def _confounder_notes() -> dict[str, ConfounderNote]:
    return {
        "recent_form": ConfounderNote(
            considered=True,
            method=(
                f"Deviation from the player's own trailing {DEFAULT_TRAILING_FORM_WINDOW}-game average "
                "as of each match (see adjusted_effect)."
            ),
            reason=None,
        ),
        "venue": ConfounderNote(
            considered=False,
            method=None,
            reason="Point-in-time venue data exists (Match.venue_id) but sample sizes here are too small to stratify by venue without overfitting.",
        ),
        "role": ConfounderNote(
            considered=False,
            method=None,
            reason="No structured position/role data exists anywhere in this codebase - see role_analysis_explanation.",
        ),
        "lineup_and_era_context": ConfounderNote(
            considered=False,
            method=None,
            reason=(
                "Games against this opponent and games against other opponents may fall in different "
                "seasons or circumstances - different teammates on the ground, different team strength, "
                "rule changes, or the opponent's own list changing over time. None of that is conditioned "
                "on here; only the player's own recent individual form is."
            ),
        ),
    }


def _opponent_confidence(games_against: int, games_other: int) -> PlayerContextConfidence:
    warnings: list[str] = []
    if games_against == 0:
        warnings.append("No recorded games found against this opponent for this player at this club.")
    if games_other == 0:
        warnings.append("No recorded games found against any other opponent for this player at this club.")

    smaller = min(games_against, games_other)
    if smaller < MIN_GAMES_INSUFFICIENT:
        tier = PlayerContextConfidenceTier.INSUFFICIENT_HISTORY
        warnings.append(
            f"Fewer than {MIN_GAMES_INSUFFICIENT} games in the smaller group - treat this split as anecdotal, not a reliable estimate."
        )
    elif smaller < LOWER_CONFIDENCE_GAMES:
        tier = PlayerContextConfidenceTier.LOWER
        warnings.append(f"Only {smaller} games in the smaller group - a real sample-size limitation.")
    elif smaller < MODERATE_CONFIDENCE_GAMES:
        tier = PlayerContextConfidenceTier.MODERATE
    else:
        tier = PlayerContextConfidenceTier.HIGHER

    return PlayerContextConfidence(tier=tier, warnings=warnings)


def _opponent_adjusted_effect(
    ordered_known_opponent_rows: Sequence[PlayerMatchStat],
    baselines: dict[int, float | None],
    opponent_match_ids: set[int],
    stat_field: str,
) -> AdjustedTeammateEffect:
    """Thin wrapper around player_context_analysis._adjusted_effect - same
    point-in-time-safe trailing-form-residual methodology and the same
    MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT gating, just re-signed and
    re-explained for the "against this opponent vs against others"
    framing (the reused function's own sign convention is "out minus in",
    i.e. other-opponents-minus-this-opponent; this module instead reports
    "against this opponent minus against other opponents" to match
    against_opponent/against_other_opponents above, so the two numbers
    are never accidentally read backwards against each other).
    """
    raw = _adjusted_effect(ordered_known_opponent_rows, baselines, opponent_match_ids, stat_field)
    if not raw.available:
        return AdjustedTeammateEffect(
            available=False,
            value=None,
            games_with_baseline_teammate_in=raw.games_with_baseline_teammate_in,
            games_with_baseline_teammate_out=raw.games_with_baseline_teammate_out,
            method=raw.method,
            explanation=(
                f"Need at least a computable recent-form baseline in {raw.games_with_baseline_teammate_in} "
                f"games against this opponent and {raw.games_with_baseline_teammate_out} against other "
                "opponents; showing the raw split only rather than an adjusted estimate that would not be "
                "reliable at this sample size."
            ),
        )
    return AdjustedTeammateEffect(
        available=True,
        value=-raw.value if raw.value is not None else None,
        games_with_baseline_teammate_in=raw.games_with_baseline_teammate_in,
        games_with_baseline_teammate_out=raw.games_with_baseline_teammate_out,
        method=raw.method,
        explanation=(
            "Computed as the difference in each game's deviation from the player's own trailing "
            f"{DEFAULT_TRAILING_FORM_WINDOW}-game average (as of that game, using no future data), "
            "comparing games against this opponent vs against other opponents. This isolates the split "
            "from the player's own recent-form trend but does not control for venue, role, or the "
            "different eras/lineups those two groups of games may span - see 'confounders' for why those "
            "are not modeled. This is an association, not a causal estimate and not a prediction."
        ),
    )


def _build_evidence(
    ordered_known_opponent_rows: Sequence[PlayerMatchStat],
    opponent_match_ids: set[int],
    matches_by_id: dict[int, Match],
    rounds_by_id: dict[int, Round],
    seasons_by_id: dict[int, Season],
    venues_by_id: dict[int, Venue],
    teams_by_id: dict[int, Team],
    stat_field: str,
) -> list[OpponentEvidenceRow]:
    rows: list[OpponentEvidenceRow] = []
    for r in ordered_known_opponent_rows:
        match = matches_by_id[r.match_id]
        round_ = rounds_by_id.get(match.round_id)
        season = seasons_by_id.get(match.season_id)
        venue = venues_by_id.get(match.venue_id) if match.venue_id is not None else None
        opponent = teams_by_id.get(r.opponent_team_id)
        is_home = match.home_team_id == r.team_id if match.home_team_id == r.team_id or match.away_team_id == r.team_id else None
        rows.append(
            OpponentEvidenceRow(
                match_id=r.match_id,
                season_year=season.year if season else 0,
                round_number=round_.round_number if round_ else 0,
                round_name=round_.name if round_ else None,
                scheduled_start=match.scheduled_start,
                team_id=r.team_id,
                team_name=teams_by_id[r.team_id].name if r.team_id in teams_by_id else "",
                opponent_team_id=r.opponent_team_id,
                opponent_name=opponent.name if opponent else "",
                venue_name=venue.name if venue else None,
                is_home=is_home,
                is_selected_opponent=r.match_id in opponent_match_ids,
                stat_value=getattr(r, stat_field),
                time_on_ground_pct=r.time_on_ground_pct,
            )
        )
    return rows


def build_opponent_context_analysis(
    db: Session,
    player_id: int,
    opponent_team_id: int,
    stat: str = "disposals",
    thresholds: Sequence[int] | None = None,
) -> OpponentContextAnalysis:
    if stat not in STAT_FIELDS:
        raise ValueError(f"Unsupported stat {stat!r} - must be one of {sorted(SUPPORTED_STATS)}")
    stat_field = STAT_FIELDS[stat]
    resolved_thresholds: tuple[int, ...] = tuple(thresholds) if thresholds else DEFAULT_THRESHOLDS[stat]

    player = db.get(Player, player_id)
    if player is None:
        raise LookupError(f"Player {player_id} not found")
    opponent_team = db.get(Team, opponent_team_id)
    if opponent_team is None:
        raise LookupError(f"Team {opponent_team_id} not found")

    scope_explanation = (
        "This history is restricted to the player's most recent recorded club "
        "(per PlayerMatchStat.team_id, the source of truth for who played for whom on a given date - "
        "not the convenience players.current_team_id field)."
    )

    player_rows: list[PlayerMatchStat] = list(
        db.scalars(
            select(PlayerMatchStat)
            .join(Match, PlayerMatchStat.match_id == Match.id)
            .where(PlayerMatchStat.player_id == player_id)
            .order_by(Match.scheduled_start.asc(), PlayerMatchStat.match_id.asc())
        ).all()
    )

    if not player_rows:
        empty = _empty_split(resolved_thresholds)
        return OpponentContextAnalysis(
            player_id=player_id,
            player_name=player.display_name,
            opponent_team_id=opponent_team_id,
            opponent_team_name=opponent_team.name,
            team_id=None,
            team_name=None,
            stat=stat,
            thresholds=resolved_thresholds,
            against_opponent=empty,
            against_other_opponents=empty,
            raw_difference=None,
            adjusted_effect=AdjustedTeammateEffect(
                available=False,
                value=None,
                games_with_baseline_teammate_in=0,
                games_with_baseline_teammate_out=0,
                method="recent_form_residual",
                explanation="No recorded match statistics found for this player.",
            ),
            confounders=_confounder_notes(),
            confidence=PlayerContextConfidence(
                tier=PlayerContextConfidenceTier.INSUFFICIENT_HISTORY,
                warnings=["No recorded match statistics found for this player."],
            ),
            evidence=[],
            role_analysis_available=False,
            role_analysis_explanation=_ROLE_UNAVAILABLE_EXPLANATION,
            scope_explanation=scope_explanation,
        )

    # Same convention as player_context_analysis / teammate_discovery.
    team_id = player_rows[-1].team_id
    team = db.get(Team, team_id)
    club_rows = [r for r in player_rows if r.team_id == team_id]
    match_ids = [r.match_id for r in club_rows]

    matches_by_id = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()}
    ordered = sorted(club_rows, key=lambda r: (matches_by_id[r.match_id].scheduled_start, r.match_id))
    # Baselines are computed over the FULL club history (including any
    # opponent-unknown games) - the player's own trailing form doesn't
    # depend on whether the opponent label happens to be recorded.
    baselines = compute_trailing_baselines(ordered, stat_field)

    # Games with an unknown opponent are excluded from both comparison
    # groups and the evidence table - never guessed into "other opponents".
    known_opponent_rows = [r for r in ordered if r.opponent_team_id is not None]

    round_ids = {m.round_id for m in matches_by_id.values()}
    rounds_by_id = {r.id: r for r in db.scalars(select(Round).where(Round.id.in_(round_ids))).all()} if round_ids else {}
    season_ids = {m.season_id for m in matches_by_id.values()}
    seasons_by_id = {s.id: s for s in db.scalars(select(Season).where(Season.id.in_(season_ids))).all()} if season_ids else {}
    venue_ids = {m.venue_id for m in matches_by_id.values() if m.venue_id is not None}
    venues_by_id = {v.id: v for v in db.scalars(select(Venue).where(Venue.id.in_(venue_ids))).all()} if venue_ids else {}
    opponent_team_ids = {r.opponent_team_id for r in known_opponent_rows}
    teams_by_id = {team_id: team} if team is not None else {}
    if opponent_team_ids:
        teams_by_id.update({t.id: t for t in db.scalars(select(Team).where(Team.id.in_(opponent_team_ids))).all()})
    teams_by_id.setdefault(opponent_team_id, opponent_team)

    opponent_match_ids = {r.match_id for r in known_opponent_rows if r.opponent_team_id == opponent_team_id}

    against_rows = [r for r in known_opponent_rows if r.match_id in opponent_match_ids]
    other_rows = [r for r in known_opponent_rows if r.match_id not in opponent_match_ids]
    against_stats = _split_stats(against_rows, stat_field, resolved_thresholds)
    other_stats = _split_stats(other_rows, stat_field, resolved_thresholds)

    raw_difference = (
        against_stats.mean - other_stats.mean if against_stats.mean is not None and other_stats.mean is not None else None
    )

    adjusted = _opponent_adjusted_effect(known_opponent_rows, baselines, opponent_match_ids, stat_field)
    confidence = _opponent_confidence(against_stats.games, other_stats.games)
    evidence = _build_evidence(
        known_opponent_rows, opponent_match_ids, matches_by_id, rounds_by_id, seasons_by_id, venues_by_id, teams_by_id, stat_field
    )
    evidence.sort(key=lambda e: e.scheduled_start, reverse=True)

    return OpponentContextAnalysis(
        player_id=player_id,
        player_name=player.display_name,
        opponent_team_id=opponent_team_id,
        opponent_team_name=opponent_team.name,
        team_id=team_id,
        team_name=team.name if team else None,
        stat=stat,
        thresholds=resolved_thresholds,
        against_opponent=against_stats,
        against_other_opponents=other_stats,
        raw_difference=raw_difference,
        adjusted_effect=adjusted,
        confounders=_confounder_notes(),
        confidence=confidence,
        evidence=evidence,
        role_analysis_available=False,
        role_analysis_explanation=_ROLE_UNAVAILABLE_EXPLANATION,
        scope_explanation=scope_explanation,
    )


def opponent_context_analysis_as_dict(analysis: OpponentContextAnalysis) -> dict:
    def _split(s: ContextSplitStats) -> dict:
        return {
            "games": s.games,
            "stat_sample_size": s.stat_sample_size,
            "mean": s.mean,
            "median": s.median,
            "milestone_rates": {f"{t}+": v for t, v in s.milestone_rates.items()},
            "average_time_on_ground_pct": s.average_time_on_ground_pct,
            "time_on_ground_sample_size": s.time_on_ground_sample_size,
        }

    return {
        "player_id": analysis.player_id,
        "player_name": analysis.player_name,
        "opponent_team_id": analysis.opponent_team_id,
        "opponent_team_name": analysis.opponent_team_name,
        "team_id": analysis.team_id,
        "team_name": analysis.team_name,
        "stat": analysis.stat,
        "thresholds": list(analysis.thresholds),
        "against_opponent": _split(analysis.against_opponent),
        "against_other_opponents": _split(analysis.against_other_opponents),
        "raw_difference": analysis.raw_difference,
        "adjusted_effect": {
            "available": analysis.adjusted_effect.available,
            "value": analysis.adjusted_effect.value,
            "games_with_baseline_against_opponent": analysis.adjusted_effect.games_with_baseline_teammate_in,
            "games_with_baseline_other_opponents": analysis.adjusted_effect.games_with_baseline_teammate_out,
            "method": analysis.adjusted_effect.method,
            "explanation": analysis.adjusted_effect.explanation,
        },
        "confounders": {
            key: {"considered": note.considered, "method": note.method, "reason": note.reason}
            for key, note in analysis.confounders.items()
        },
        "confidence": {"tier": analysis.confidence.tier.value, "warnings": analysis.confidence.warnings},
        "evidence": [
            {
                "match_id": e.match_id,
                "season_year": e.season_year,
                "round_number": e.round_number,
                "round_name": e.round_name,
                "scheduled_start": e.scheduled_start,
                "team_id": e.team_id,
                "team_name": e.team_name,
                "opponent_team_id": e.opponent_team_id,
                "opponent_name": e.opponent_name,
                "venue_name": e.venue_name,
                "is_home": e.is_home,
                "is_selected_opponent": e.is_selected_opponent,
                "stat_value": e.stat_value,
                "time_on_ground_pct": e.time_on_ground_pct,
            }
            for e in analysis.evidence
        ],
        "role_analysis_available": analysis.role_analysis_available,
        "role_analysis_explanation": analysis.role_analysis_explanation,
        "scope_explanation": analysis.scope_explanation,
    }
