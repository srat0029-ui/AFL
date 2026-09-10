"""Player context analysis - "how does Player X perform when Teammate Y is
in or out" and the related evidence/confidence/adjustment machinery behind
it (the Player Context Research product direction).

Design principles, matching the brief this was built from:
  - A raw with/without split is never presented as causal on its own - see
    `adjusted_effect` below and its accompanying `confounders` report.
  - An adjusted effect is only ever returned when it is actually
    computable from real, point-in-time-safe data (see
    `compute_trailing_baselines`); otherwise `adjusted_effect.available`
    is False with a plain-English reason - never an invented number.
  - Confounders this codebase cannot reliably condition on today
    (opponent strength, venue, role) are reported as NOT considered, with
    a reason, rather than silently ignored or faked.
  - Confidence tiering follows the same rule-based, named-constant
    convention as app/player_modelling/disposal_confidence.py - no
    fitted/fake confidence score.

Team-context resolution: "teammate" is only meaningful for a specific
club, and a player's club changes over a career (trades, delistings).
Following PlayerMatchStat's own documented convention (team_id on that
table, NOT players.current_team_id, is the source of truth for who played
for whom on a given date), this module restricts the whole analysis to
the player's most recent club as evidenced by their own match rows, and
reports which club that was so the caller can see exactly what was
compared.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.match import Match
from app.models.player import Player
from app.models.player_match_stat import PlayerMatchStat
from app.models.round import Round
from app.models.season import Season
from app.models.team import Team
from app.models.venue import Venue

# The stat markets this project already models as two independent
# projections elsewhere (disposal/goal) - deliberately not a fully generic
# "any numeric column" system, to avoid exposing meaningless splits (e.g.
# jumper_number).
STAT_FIELDS: dict[str, str] = {"disposals": "disposals", "goals": "goals"}
SUPPORTED_STATS = frozenset(STAT_FIELDS)

DEFAULT_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "disposals": (15, 20, 25, 30),
    "goals": (1, 2, 3),
}

# Recent-form baseline window and minimum prior-game requirement for the
# adjusted effect - a reasonable default "form" horizon, not empirically
# tuned; kept as a named constant so it's easy to revisit.
DEFAULT_TRAILING_FORM_WINDOW = 10
MIN_TRAILING_GAMES_FOR_BASELINE = 3

# Need at least this many baseline-eligible games in EACH group before an
# adjusted estimate is reported at all - below this, a mean-of-residuals
# is not a reliable estimate of anything.
MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT = 5

# Confidence tiers for the raw split, keyed off the SMALLER of the two
# group sizes (that's the constraining sample) - same rule-based
# philosophy as disposal_confidence.ConfidenceTier, tailored thresholds.
MIN_GAMES_INSUFFICIENT = 3
LOWER_CONFIDENCE_GAMES = 8
MODERATE_CONFIDENCE_GAMES = 15

_ROLE_UNAVAILABLE_EXPLANATION = (
    "No structured position/role data exists in this codebase (verified by a repo-wide search) - "
    "ExpectedLineup.role_note is a free-text field entered manually per upcoming match only, not a "
    "systematic historical record. A role-conditioned split or projection cannot be computed "
    "reliably, so this is reported as unavailable rather than inventing a role-based adjustment."
)


class PlayerContextConfidenceTier(str, Enum):
    INSUFFICIENT_HISTORY = "insufficient_history"
    LOWER = "lower_confidence"
    MODERATE = "moderate_confidence"
    HIGHER = "higher_confidence"


@dataclass(frozen=True)
class ContextSplitStats:
    games: int
    stat_sample_size: int
    mean: float | None
    median: float | None
    milestone_rates: dict[int, float | None]
    average_time_on_ground_pct: float | None
    time_on_ground_sample_size: int


@dataclass(frozen=True)
class ContextEvidenceRow:
    match_id: int
    season_year: int
    round_number: int
    round_name: str | None
    scheduled_start: datetime
    team_id: int
    team_name: str
    opponent_team_id: int | None
    opponent_name: str | None
    venue_name: str | None
    teammate_played: bool
    stat_value: int | None
    time_on_ground_pct: int | None


@dataclass(frozen=True)
class ConfounderNote:
    considered: bool
    method: str | None
    reason: str | None


@dataclass(frozen=True)
class AdjustedTeammateEffect:
    available: bool
    value: float | None
    games_with_baseline_teammate_in: int
    games_with_baseline_teammate_out: int
    method: str
    explanation: str


@dataclass(frozen=True)
class PlayerContextConfidence:
    tier: PlayerContextConfidenceTier
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlayerContextAnalysis:
    player_id: int
    player_name: str
    teammate_id: int
    teammate_name: str
    team_id: int | None
    team_name: str | None
    stat: str
    thresholds: tuple[int, ...]
    with_teammate: ContextSplitStats
    without_teammate: ContextSplitStats
    raw_difference: float | None  # without_teammate.mean - with_teammate.mean
    adjusted_effect: AdjustedTeammateEffect
    confounders: dict[str, ConfounderNote]
    confidence: PlayerContextConfidence
    evidence: list[ContextEvidenceRow]
    role_analysis_available: bool
    role_analysis_explanation: str


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
        "opponent_strength": ConfounderNote(
            considered=False,
            method=None,
            reason="No opponent-strength/rating model exists in this codebase to condition on without introducing an unvalidated confounder.",
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
    }


def _empty_split(thresholds: Sequence[int]) -> ContextSplitStats:
    return ContextSplitStats(
        games=0,
        stat_sample_size=0,
        mean=None,
        median=None,
        milestone_rates={t: None for t in thresholds},
        average_time_on_ground_pct=None,
        time_on_ground_sample_size=0,
    )


def _split_stats(rows: Sequence[PlayerMatchStat], stat_field: str, thresholds: Sequence[int]) -> ContextSplitStats:
    values = [v for r in rows if (v := getattr(r, stat_field)) is not None]
    tog_values = [r.time_on_ground_pct for r in rows if r.time_on_ground_pct is not None]
    milestone_rates = (
        {t: sum(1 for v in values if v >= t) / len(values) for t in thresholds} if values else {t: None for t in thresholds}
    )
    return ContextSplitStats(
        games=len(rows),
        stat_sample_size=len(values),
        mean=statistics.fmean(values) if values else None,
        median=statistics.median(values) if values else None,
        milestone_rates=milestone_rates,
        average_time_on_ground_pct=statistics.fmean(tog_values) if tog_values else None,
        time_on_ground_sample_size=len(tog_values),
    )


def compute_trailing_baselines(
    ordered_rows: Sequence[object],
    stat_field: str,
    window: int = DEFAULT_TRAILING_FORM_WINDOW,
    min_games: int = MIN_TRAILING_GAMES_FOR_BASELINE,
) -> dict[int, float | None]:
    """A point-in-time-safe trailing-average baseline per row, keyed by
    row.id. `ordered_rows` MUST already be in chronological order. Each
    row's baseline uses only strictly EARLIER rows' stat values (never the
    row's own value, never a later one) - this is what makes the resulting
    adjusted effect a genuine confound control rather than lookahead bias.
    A row gets no baseline (None) until at least `min_games` prior rows
    with a non-null stat value have accumulated.
    """
    baselines: dict[int, float | None] = {}
    history: list[float] = []
    for row in ordered_rows:
        baselines[row.id] = statistics.fmean(history[-window:]) if len(history) >= min_games else None
        value = getattr(row, stat_field)
        if value is not None:
            history.append(value)
    return baselines


def _adjusted_effect(
    ordered_rows: Sequence[PlayerMatchStat],
    baselines: dict[int, float | None],
    teammate_match_ids: set[int],
    stat_field: str,
) -> AdjustedTeammateEffect:
    residuals_in: list[float] = []
    residuals_out: list[float] = []
    for row in ordered_rows:
        baseline = baselines.get(row.id)
        value = getattr(row, stat_field)
        if baseline is None or value is None:
            continue
        residual = value - baseline
        (residuals_in if row.match_id in teammate_match_ids else residuals_out).append(residual)

    if len(residuals_in) < MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT or len(residuals_out) < MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT:
        return AdjustedTeammateEffect(
            available=False,
            value=None,
            games_with_baseline_teammate_in=len(residuals_in),
            games_with_baseline_teammate_out=len(residuals_out),
            method="recent_form_residual",
            explanation=(
                f"Need at least {MIN_GAMES_PER_GROUP_FOR_ADJUSTMENT} games with a computable recent-form "
                f"baseline in each group; found {len(residuals_in)} with the teammate in and "
                f"{len(residuals_out)} with the teammate out. Showing the raw split only rather than an "
                "adjusted estimate that would not be reliable at this sample size."
            ),
        )

    value = statistics.fmean(residuals_out) - statistics.fmean(residuals_in)
    return AdjustedTeammateEffect(
        available=True,
        value=value,
        games_with_baseline_teammate_in=len(residuals_in),
        games_with_baseline_teammate_out=len(residuals_out),
        method="recent_form_residual",
        explanation=(
            "Computed as the difference in each game's deviation from the player's own trailing "
            f"{DEFAULT_TRAILING_FORM_WINDOW}-game average (as of that game, using no future data), "
            "comparing games with the teammate out vs in. This isolates the split from the player's own "
            "recent-form trend but does not control for opponent strength, venue, or role - see "
            "'confounders' for why those are not modeled. This is an association, not a causal estimate."
        ),
    )


def _confidence(games_in: int, games_out: int) -> PlayerContextConfidence:
    warnings: list[str] = []
    if games_in == 0:
        warnings.append("No games found where the teammate played alongside this player at this club.")
    if games_out == 0:
        warnings.append("No games found where the teammate was absent while this player was at this club.")

    smaller = min(games_in, games_out)
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


def _build_evidence(
    ordered_rows: Sequence[PlayerMatchStat],
    teammate_match_ids: set[int],
    matches_by_id: dict[int, Match],
    rounds_by_id: dict[int, Round],
    seasons_by_id: dict[int, Season],
    venues_by_id: dict[int, Venue],
    teams_by_id: dict[int, Team],
    stat_field: str,
) -> list[ContextEvidenceRow]:
    rows: list[ContextEvidenceRow] = []
    for r in ordered_rows:
        match = matches_by_id[r.match_id]
        round_ = rounds_by_id.get(match.round_id)
        season = seasons_by_id.get(match.season_id)
        venue = venues_by_id.get(match.venue_id) if match.venue_id is not None else None
        opponent = teams_by_id.get(r.opponent_team_id) if r.opponent_team_id is not None else None
        rows.append(
            ContextEvidenceRow(
                match_id=r.match_id,
                season_year=season.year if season else 0,
                round_number=round_.round_number if round_ else 0,
                round_name=round_.name if round_ else None,
                scheduled_start=match.scheduled_start,
                team_id=r.team_id,
                team_name=teams_by_id[r.team_id].name if r.team_id in teams_by_id else "",
                opponent_team_id=r.opponent_team_id,
                opponent_name=opponent.name if opponent else None,
                venue_name=venue.name if venue else None,
                teammate_played=r.match_id in teammate_match_ids,
                stat_value=getattr(r, stat_field),
                time_on_ground_pct=r.time_on_ground_pct,
            )
        )
    return rows


def build_player_context_analysis(
    db: Session,
    player_id: int,
    teammate_id: int,
    stat: str = "disposals",
    thresholds: Sequence[int] | None = None,
) -> PlayerContextAnalysis:
    if stat not in STAT_FIELDS:
        raise ValueError(f"Unsupported stat {stat!r} - must be one of {sorted(SUPPORTED_STATS)}")
    stat_field = STAT_FIELDS[stat]
    resolved_thresholds: tuple[int, ...] = tuple(thresholds) if thresholds else DEFAULT_THRESHOLDS[stat]

    player = db.get(Player, player_id)
    if player is None:
        raise LookupError(f"Player {player_id} not found")
    teammate = db.get(Player, teammate_id)
    if teammate is None:
        raise LookupError(f"Player {teammate_id} not found")

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
        return PlayerContextAnalysis(
            player_id=player_id,
            player_name=player.display_name,
            teammate_id=teammate_id,
            teammate_name=teammate.display_name,
            team_id=None,
            team_name=None,
            stat=stat,
            thresholds=resolved_thresholds,
            with_teammate=empty,
            without_teammate=empty,
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
        )

    # Restrict to the player's most recent club, per PlayerMatchStat.team_id
    # (not players.current_team_id - see that model's own docstring).
    team_id = player_rows[-1].team_id
    team = db.get(Team, team_id)
    club_rows = [r for r in player_rows if r.team_id == team_id]
    match_ids = [r.match_id for r in club_rows]

    teammate_match_ids = set(
        db.scalars(
            select(PlayerMatchStat.match_id).where(
                PlayerMatchStat.player_id == teammate_id,
                PlayerMatchStat.team_id == team_id,
                PlayerMatchStat.match_id.in_(match_ids),
            )
        ).all()
    )

    matches_by_id = {m.id: m for m in db.scalars(select(Match).where(Match.id.in_(match_ids))).all()}
    round_ids = {m.round_id for m in matches_by_id.values()}
    rounds_by_id = {r.id: r for r in db.scalars(select(Round).where(Round.id.in_(round_ids))).all()} if round_ids else {}
    season_ids = {m.season_id for m in matches_by_id.values()}
    seasons_by_id = {s.id: s for s in db.scalars(select(Season).where(Season.id.in_(season_ids))).all()} if season_ids else {}
    venue_ids = {m.venue_id for m in matches_by_id.values() if m.venue_id is not None}
    venues_by_id = {v.id: v for v in db.scalars(select(Venue).where(Venue.id.in_(venue_ids))).all()} if venue_ids else {}
    opponent_team_ids = {r.opponent_team_id for r in club_rows if r.opponent_team_id is not None}
    teams_by_id = {team_id: team} if team is not None else {}
    if opponent_team_ids:
        teams_by_id.update({t.id: t for t in db.scalars(select(Team).where(Team.id.in_(opponent_team_ids))).all()})

    ordered = sorted(club_rows, key=lambda r: (matches_by_id[r.match_id].scheduled_start, r.match_id))
    baselines = compute_trailing_baselines(ordered, stat_field)

    with_rows = [r for r in ordered if r.match_id in teammate_match_ids]
    without_rows = [r for r in ordered if r.match_id not in teammate_match_ids]
    with_stats = _split_stats(with_rows, stat_field, resolved_thresholds)
    without_stats = _split_stats(without_rows, stat_field, resolved_thresholds)

    raw_difference = (
        without_stats.mean - with_stats.mean if with_stats.mean is not None and without_stats.mean is not None else None
    )

    adjusted = _adjusted_effect(ordered, baselines, teammate_match_ids, stat_field)
    confidence = _confidence(with_stats.games, without_stats.games)
    evidence = _build_evidence(
        ordered, teammate_match_ids, matches_by_id, rounds_by_id, seasons_by_id, venues_by_id, teams_by_id, stat_field
    )
    evidence.sort(key=lambda e: e.scheduled_start, reverse=True)

    return PlayerContextAnalysis(
        player_id=player_id,
        player_name=player.display_name,
        teammate_id=teammate_id,
        teammate_name=teammate.display_name,
        team_id=team_id,
        team_name=team.name if team else None,
        stat=stat,
        thresholds=resolved_thresholds,
        with_teammate=with_stats,
        without_teammate=without_stats,
        raw_difference=raw_difference,
        adjusted_effect=adjusted,
        confounders=_confounder_notes(),
        confidence=confidence,
        evidence=evidence,
        role_analysis_available=False,
        role_analysis_explanation=_ROLE_UNAVAILABLE_EXPLANATION,
    )


def player_context_analysis_as_dict(analysis: PlayerContextAnalysis) -> dict:
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
        "teammate_id": analysis.teammate_id,
        "teammate_name": analysis.teammate_name,
        "team_id": analysis.team_id,
        "team_name": analysis.team_name,
        "stat": analysis.stat,
        "thresholds": list(analysis.thresholds),
        "with_teammate": _split(analysis.with_teammate),
        "without_teammate": _split(analysis.without_teammate),
        "raw_difference": analysis.raw_difference,
        "adjusted_effect": {
            "available": analysis.adjusted_effect.available,
            "value": analysis.adjusted_effect.value,
            "games_with_baseline_teammate_in": analysis.adjusted_effect.games_with_baseline_teammate_in,
            "games_with_baseline_teammate_out": analysis.adjusted_effect.games_with_baseline_teammate_out,
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
                "teammate_played": e.teammate_played,
                "stat_value": e.stat_value,
                "time_on_ground_pct": e.time_on_ground_pct,
            }
            for e in analysis.evidence
        ],
        "role_analysis_available": analysis.role_analysis_available,
        "role_analysis_explanation": analysis.role_analysis_explanation,
    }
