"""Point-in-time feature building for the goals market — mirrors
disposal_features.py's exact leakage discipline (rows walked in strict
chronological order; a row's own actual stats are folded into history only
AFTER its features are built), adapted to goal-specific signal: scoring
history/conversion, forward-involvement proxies (marks inside 50, inside
50s - real fields this dataset actually has, not invented position data),
and team/opponent scoring environment instead of disposal environment.

Feature groups (see PLAYER_FEATURE_NAMES for the exact final list):
  - scoring history: last3/5/10 goals average, season/career average, EWMA,
    recent goal-conversion rate (goals / (goals+behinds), career and
    last-10), recent zero-goal rate (last10), recent rate of 1+/2+/3+ games
  - forward involvement: marks_inside_50, inside_50s, scoring shots
    (goals+behinds), goal_assists, marks, disposals - all last-5 rolling
    averages, all from this player's own PRIOR games
  - team context: team's own recent goals/inside-50 environment (from
    TeamGoalGameRow), pre-match Elo win probability and Poisson expected
    scores/margin (same leakage-safe walk-forward reuse as disposals - see
    disposal_team_context.py, shared here since it's market-agnostic)
  - opponent context: upcoming opponent's recent goals/scoring-shots
    conceded
  - venue: shrunk average goals-per-player-game at this venue
  - home/away flag
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from app.player_modelling.goal_data import PlayerGoalGameRow, TeamGoalGameRow
from app.player_modelling.market import PlayerMarket

LEAGUE_AVG_GOALS_FALLBACK = 0.53  # audited dataset mean (see goal audit) - used only before any data observed
VENUE_SHRINKAGE_K = 20.0
_ROLLING_WINDOW = 10
EWMA_ALPHA = 0.3


@dataclass(frozen=True)
class GoalFeatureRow:
    player_id: int
    match_id: int
    team_id: int
    opponent_team_id: int
    season_year: int
    round_number: int
    is_final: bool
    scheduled_start: datetime
    goals: int
    games_of_history: int
    features: dict[str, float | None] = field(default_factory=dict)


PLAYER_FEATURE_NAMES: tuple[str, ...] = (
    "goals_last3_avg",
    "goals_last5_avg",
    "goals_last10_avg",
    "goals_season_avg",
    "goals_career_avg",
    "goals_ewma",
    "goals_last5_std",
    "conversion_rate_career",
    "conversion_rate_last10",
    "zero_goal_rate_last10",
    "rate_1plus_last10",
    "rate_2plus_last10",
    "rate_3plus_last10",
    "marks_inside_50_last5_avg",
    "inside_50s_last5_avg",
    "scoring_shots_last5_avg",
    "goal_assists_last5_avg",
    "marks_last5_avg",
    "disposals_last5_avg",
    "tog_last5_avg",
    "team_recent_goals_avg",
    "team_recent_inside50_avg",
    "opponent_goals_conceded_avg",
    "opponent_scoring_shots_conceded_avg",
    "venue_goals_env",
    "team_elo_win_prob",
    "team_expected_score",
    "opponent_expected_score",
    "expected_margin",
    "is_home",
)


@dataclass
class _PlayerScoringHistory:
    goals: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW))
    behinds: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW))
    marks_inside_50: deque = field(default_factory=lambda: deque(maxlen=5))
    inside_50s: deque = field(default_factory=lambda: deque(maxlen=5))
    goal_assists: deque = field(default_factory=lambda: deque(maxlen=5))
    marks: deque = field(default_factory=lambda: deque(maxlen=5))
    disposals: deque = field(default_factory=lambda: deque(maxlen=5))
    tog: deque = field(default_factory=lambda: deque(maxlen=5))
    season_goals: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    career_goal_sum: float = 0.0
    career_behind_sum: float = 0.0
    n_games: int = 0
    ewma_goals: float | None = None


def _avg(values) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _std(values) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def _rate_at_least(values, threshold: int) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(1 for v in vals if v >= threshold) / len(vals) if vals else None


def _shrink(observed: float | None, n: int, league_avg: float, k: float = VENUE_SHRINKAGE_K) -> float:
    if observed is None or n == 0:
        return league_avg
    weight = n / (n + k)
    return weight * observed + (1 - weight) * league_avg


class GoalFeatureBuilder:
    market = PlayerMarket.GOALS

    def __init__(self, team_context: dict[int, dict] | None = None, season_scale_factors: dict[int, float] | None = None):
        """team_context: same leakage-safe Elo/Poisson walk-forward summary
        disposal_team_context.py produces (market-agnostic - reused as-is).
        season_scale_factors: same 2020-adjustment mechanism as
        DisposalFeatureBuilder (see its docstring) - applied only to how a
        row's goals/behinds/involvement feed rolling HISTORY, never to the
        row's own target goals value."""
        self._team_context = team_context or {}
        self._season_scale_factors = season_scale_factors or {}

    def build(self, player_rows: list[PlayerGoalGameRow], team_rows: list[TeamGoalGameRow] | None = None) -> list[GoalFeatureRow]:
        team_rows = team_rows or []
        player_rows_sorted = sorted(player_rows, key=lambda r: (r.scheduled_start, r.match_id, r.player_id))
        team_rows_sorted = sorted(team_rows, key=lambda r: (r.scheduled_start, r.match_id))

        histories: dict[int, _PlayerScoringHistory] = defaultdict(_PlayerScoringHistory)
        team_goals_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        team_i50_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        goals_conceded_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        scoring_shots_conceded_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        venue_totals: dict[int, list[float]] = defaultdict(list)

        team_ctx_by_match: dict[int, dict[int, dict]] = defaultdict(dict)
        matches_order: list[int] = []
        rows_by_match: dict[int, list[TeamGoalGameRow]] = defaultdict(list)
        for t in team_rows_sorted:
            if t.match_id not in rows_by_match:
                matches_order.append(t.match_id)
            rows_by_match[t.match_id].append(t)

        for match_id in matches_order:
            pair = rows_by_match[match_id]
            for t in pair:
                team_ctx_by_match[t.match_id][t.team_id] = {
                    "team_recent_goals_avg": _avg(team_goals_hist[t.team_id]),
                    "team_recent_inside50_avg": _avg(team_i50_hist[t.team_id]),
                    "opponent_goals_conceded_avg": _avg(goals_conceded_hist[t.opponent_team_id]) if t.opponent_team_id else None,
                    "opponent_scoring_shots_conceded_avg": (
                        _avg(scoring_shots_conceded_hist[t.opponent_team_id]) if t.opponent_team_id else None
                    ),
                }
            for t in pair:
                if t.goals is not None:
                    team_goals_hist[t.team_id].append(t.goals)
                    if t.opponent_team_id is not None:
                        goals_conceded_hist[t.opponent_team_id].append(t.goals)
                        if t.behinds is not None:
                            scoring_shots_conceded_hist[t.opponent_team_id].append(t.goals + t.behinds)
                if t.inside_50s is not None:
                    team_i50_hist[t.team_id].append(t.inside_50s)

        result: list[GoalFeatureRow] = []
        league_sum, league_n = 0.0, 0

        for row in player_rows_sorted:
            hist = histories[row.player_id]
            league_avg = league_sum / league_n if league_n else LEAGUE_AVG_GOALS_FALLBACK

            team_snapshot = team_ctx_by_match.get(row.match_id, {}).get(row.team_id, {})

            venue_hist = venue_totals.get(row.venue_id, []) if row.venue_id is not None else []
            venue_env = _shrink(_avg(venue_hist), len(venue_hist), league_avg)

            ctx = self._team_context.get(row.match_id, {}).get(row.team_id, {})

            career_scoring_shots = hist.career_goal_sum + hist.career_behind_sum
            conversion_career = (hist.career_goal_sum / career_scoring_shots) if career_scoring_shots > 0 else None
            last10_goals = list(hist.goals)
            last10_behinds = list(hist.behinds)
            last10_shots_sum = sum(g for g in last10_goals if g is not None) + sum(b for b in last10_behinds if b is not None)
            conversion_last10 = (
                sum(g for g in last10_goals if g is not None) / last10_shots_sum if last10_shots_sum > 0 else None
            )

            scoring_shots_last5 = None
            g5 = list(hist.goals)[-5:]
            b5 = list(hist.behinds)[-5:]
            if g5 or b5:
                gvals = [v for v in g5 if v is not None]
                bvals = [v for v in b5 if v is not None]
                if gvals or bvals:
                    scoring_shots_last5 = (sum(gvals) if gvals else 0) + (sum(bvals) if bvals else 0)
                    n_pairs = max(len(gvals), len(bvals), 1)
                    scoring_shots_last5 = scoring_shots_last5 / n_pairs

            features = {
                "goals_last3_avg": _avg(list(hist.goals)[-3:]),
                "goals_last5_avg": _avg(list(hist.goals)[-5:]),
                "goals_last10_avg": _avg(hist.goals),
                "goals_season_avg": _avg(hist.season_goals.get(row.season_year, [])) or None,
                "goals_career_avg": (hist.career_goal_sum / hist.n_games) if hist.n_games else None,
                "goals_ewma": hist.ewma_goals,
                "goals_last5_std": _std(list(hist.goals)[-5:]),
                "conversion_rate_career": conversion_career,
                "conversion_rate_last10": conversion_last10,
                "zero_goal_rate_last10": (1 - _rate_at_least(hist.goals, 1)) if hist.goals else None,
                "rate_1plus_last10": _rate_at_least(hist.goals, 1),
                "rate_2plus_last10": _rate_at_least(hist.goals, 2),
                "rate_3plus_last10": _rate_at_least(hist.goals, 3),
                "marks_inside_50_last5_avg": _avg(hist.marks_inside_50),
                "inside_50s_last5_avg": _avg(hist.inside_50s),
                "scoring_shots_last5_avg": scoring_shots_last5,
                "goal_assists_last5_avg": _avg(hist.goal_assists),
                "marks_last5_avg": _avg(hist.marks),
                "disposals_last5_avg": _avg(hist.disposals),
                "tog_last5_avg": _avg(hist.tog),
                "team_recent_goals_avg": team_snapshot.get("team_recent_goals_avg"),
                "team_recent_inside50_avg": team_snapshot.get("team_recent_inside50_avg"),
                "opponent_goals_conceded_avg": team_snapshot.get("opponent_goals_conceded_avg"),
                "opponent_scoring_shots_conceded_avg": team_snapshot.get("opponent_scoring_shots_conceded_avg"),
                "venue_goals_env": venue_env,
                "team_elo_win_prob": ctx.get("elo_win_prob"),
                "team_expected_score": ctx.get("expected_score"),
                "opponent_expected_score": ctx.get("opponent_expected_score"),
                "expected_margin": ctx.get("expected_margin"),
                "is_home": 1.0 if row.is_home else 0.0,
            }

            result.append(
                GoalFeatureRow(
                    player_id=row.player_id,
                    match_id=row.match_id,
                    team_id=row.team_id,
                    opponent_team_id=row.opponent_team_id,
                    season_year=row.season_year,
                    round_number=row.round_number,
                    is_final=row.is_final,
                    scheduled_start=row.scheduled_start,
                    goals=row.goals,
                    games_of_history=hist.n_games,
                    features=features,
                )
            )

            # --- update state with this row's own actual result, AFTER building its features ---
            scale_factor = self._season_scale_factors.get(row.season_year, 1.0)
            scaled_goals = row.goals * scale_factor
            scaled_behinds = row.behinds * scale_factor if row.behinds is not None else None

            hist.goals.append(scaled_goals)
            hist.behinds.append(scaled_behinds)
            hist.marks_inside_50.append(row.marks_inside_50 * scale_factor if row.marks_inside_50 is not None else None)
            hist.inside_50s.append(row.inside_50s * scale_factor if row.inside_50s is not None else None)
            hist.goal_assists.append(row.goal_assists * scale_factor if row.goal_assists is not None else None)
            hist.marks.append(row.marks * scale_factor if row.marks is not None else None)
            hist.disposals.append(row.disposals * scale_factor if row.disposals is not None else None)
            if row.time_on_ground_pct is not None:
                hist.tog.append(row.time_on_ground_pct)
            hist.season_goals[row.season_year].append(scaled_goals)
            hist.career_goal_sum += scaled_goals
            hist.career_behind_sum += scaled_behinds or 0.0
            hist.n_games += 1
            hist.ewma_goals = scaled_goals if hist.ewma_goals is None else EWMA_ALPHA * scaled_goals + (1 - EWMA_ALPHA) * hist.ewma_goals

            if row.venue_id is not None:
                venue_totals[row.venue_id].append(row.goals)
            league_sum += row.goals
            league_n += 1

        return result
