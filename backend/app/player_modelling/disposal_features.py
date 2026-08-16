"""Point-in-time feature building for the disposals market — the real
implementation of app/player_modelling/features.py's PlayerFeatureBuilder
interface for PlayerMarket.DISPOSALS.

Leakage discipline (non-negotiable, see tests/test_disposal_features.py):
rows are walked in strict chronological order (scheduled_start, match_id).
For each player-game row, features are computed from that player's/team's
STRICTLY PRIOR state only — the row's own actual stats (including its own
disposals, its own TOG) are folded into that player's/team's history only
AFTER its features have been produced. Feeding future games into the
history used to build an earlier row's features is never structurally
possible, since the "update" step for row i strictly follows the "build"
step for row i in the same forward pass.

Feature groups (see PLAYER_FEATURE_NAMES for the exact final list):
  - recent disposals: last3/5/10 average, season-to-date, career-to-date,
    last5 median, last5/last10 std
  - recent involvement: kicks/handballs/marks/tackles/clearances/inside_50s/
    contested_possessions/uncontested_possessions, 5-game rolling average
  - time on ground: last3/5/10 TOG average, career disposals-per-100%-TOG
    rate, TOG trend (last3 - prior7). Built from this player's own PRIOR
    rows' TOG only - never the target match's TOG, which the model must not
    see (see PlayerGameRow docstring and the adversarial leakage tests).
  - form/trend: last3-vs-last10 disposal delta (short vs long window)
  - team context: this team's own recent disposal environment (rolling
    average of TEAM total disposals, from TeamMatchStat, strictly prior),
    plus this match's pre-match Elo win probability and Poisson expected
    scores/margin for the two teams involved - both already produced by a
    leakage-safe walk-forward replay (see disposal_team_context.py), so
    reusing them here introduces no new leakage risk.
  - opponent context: the upcoming opponent's own recent disposals CONCEDED
    (rolling average, from that opponent's own prior matches, i.e. how many
    disposals its past opponents recorded against it)
  - venue: average disposals-per-player-game at this venue historically,
    shrunk toward the league mean when the venue has little history
    (see _shrink)
  - home/away: a plain 0/1 flag

Position-specific opponent features are deliberately NOT built - the brief
this implements explicitly excludes them, since reliable historical
player-position data isn't available in this dataset.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from app.player_modelling.disposal_data import PlayerGameRow, TeamGameRow
from app.player_modelling.market import PlayerMarket

MIN_GAMES_FOR_HISTORY = 1  # a player with zero prior games has no player-history signal at all
LEAGUE_AVG_DISPOSALS_FALLBACK = 16.0  # audited dataset mean (see disposal audit) - used only before any data observed
VENUE_SHRINKAGE_K = 20.0  # games of venue history at which the venue estimate gets half-weight vs league average
_ROLLING_WINDOW = 10  # longest window any feature uses - deques are capped here
EWMA_ALPHA = 0.3  # standard-ish decay for a recency-weighted disposal average (Baseline C)


@dataclass(frozen=True)
class DisposalFeatureRow:
    """One player-game's computed features plus enough identifying/label
    context for backtesting - the unit disposal_backtest.py fits/evaluates
    models on. `disposals` is the actual outcome, carried alongside the
    features (not used to build them) purely so downstream code has a
    single row type with both X and y."""

    player_id: int
    match_id: int
    team_id: int
    opponent_team_id: int
    season_year: int
    round_number: int
    is_final: bool
    scheduled_start: datetime
    disposals: int
    games_of_history: int
    features: dict[str, float | None] = field(default_factory=dict)


PLAYER_FEATURE_NAMES: tuple[str, ...] = (
    "disposals_last3_avg",
    "disposals_last5_avg",
    "disposals_last10_avg",
    "disposals_season_avg",
    "disposals_career_avg",
    "disposals_last5_median",
    "disposals_last5_std",
    "disposals_last10_std",
    "disposals_ewma",
    "kicks_last5_avg",
    "handballs_last5_avg",
    "marks_last5_avg",
    "tackles_last5_avg",
    "clearances_last5_avg",
    "inside_50s_last5_avg",
    "contested_possessions_last5_avg",
    "uncontested_possessions_last5_avg",
    "tog_last3_avg",
    "tog_last5_avg",
    "tog_last10_avg",
    "disposals_per_tog100_career",
    "tog_trend",
    "disposal_trend",
    "team_recent_disposals_avg",
    "opponent_disposals_conceded_avg",
    "venue_disposals_env",
    "team_elo_win_prob",
    "team_expected_score",
    "opponent_expected_score",
    "expected_margin",
    "is_home",
)

# The promoted disposals config's actual feature set - deliberately a
# proper SUBSET of PLAYER_FEATURE_NAMES, not the full list. A real
# ambiguity check (comparing this exact Ridge configuration against the
# all-features Ridge on the identical eval set - see
# app/player_modelling/disposal_cli.py) found it genuinely equal-or-better
# on MAE/RMSE/bias with statistically indistinguishable calibration and
# interval coverage, despite dropping team_context and tog entirely. Kept
# as a named constant (not re-derived from disposal_analysis.FEATURE_GROUPS
# at persistence time) so the promoted model's feature set is pinned and
# explicit, not silently drifting if the ablation groups are edited later.
PROMOTED_DISPOSAL_FEATURE_NAMES: tuple[str, ...] = (
    "disposals_last3_avg",
    "disposals_last5_avg",
    "disposals_last10_avg",
    "disposals_season_avg",
    "disposals_career_avg",
    "disposals_last5_median",
    "disposals_last5_std",
    "disposals_last10_std",
    "disposals_ewma",
    "kicks_last5_avg",
    "handballs_last5_avg",
    "marks_last5_avg",
    "tackles_last5_avg",
    "clearances_last5_avg",
    "inside_50s_last5_avg",
    "contested_possessions_last5_avg",
    "uncontested_possessions_last5_avg",
    "disposal_trend",
    "opponent_disposals_conceded_avg",
    "opponent_expected_score",
)


@dataclass
class _PlayerHistory:
    disposals: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW))
    kicks: deque = field(default_factory=lambda: deque(maxlen=5))
    handballs: deque = field(default_factory=lambda: deque(maxlen=5))
    marks: deque = field(default_factory=lambda: deque(maxlen=5))
    tackles: deque = field(default_factory=lambda: deque(maxlen=5))
    clearances: deque = field(default_factory=lambda: deque(maxlen=5))
    inside_50s: deque = field(default_factory=lambda: deque(maxlen=5))
    contested_possessions: deque = field(default_factory=lambda: deque(maxlen=5))
    uncontested_possessions: deque = field(default_factory=lambda: deque(maxlen=5))
    tog: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW))
    season_disposals: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    career_disposal_sum: float = 0.0  # float, not int: season_scale_factors (see DisposalFeatureBuilder) can scale this
    career_tog_sum: float = 0.0  # sum of tog_pct/100, for the disposals-per-100%-TOG rate
    n_games: int = 0
    ewma_disposals: float | None = None  # exponentially-weighted average, alpha=EWMA_ALPHA below


def _avg(values) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _std(values) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def _median(values) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _shrink(observed: float | None, n: int, league_avg: float, k: float = VENUE_SHRINKAGE_K) -> float:
    """Blends an observed rate toward a league-wide average, weighted by
    how much history backs the observed rate - n=0 returns the league
    average untouched, large n approaches the observed value. Same
    shrinkage idea app/modelling/poisson_model.py's PoissonConfig already
    uses for small-sample team strength, applied here to venues."""
    if observed is None or n == 0:
        return league_avg
    weight = n / (n + k)
    return weight * observed + (1 - weight) * league_avg


class DisposalFeatureBuilder:
    market = PlayerMarket.DISPOSALS

    def __init__(self, team_context: dict[int, dict] | None = None, season_scale_factors: dict[int, float] | None = None):
        """team_context: {match_id: {team_id: {"elo_win_prob", "expected_score",
        "opponent_expected_score", "expected_margin"}}} - a leakage-safe,
        pre-computed walk-forward summary (see disposal_team_context.py).
        Optional so features can still be built (without team-context
        columns) for tests that don't need the full Elo/Poisson replay.

        season_scale_factors: {season_year: multiplier} applied ONLY to how
        a row's disposal/involvement counts feed into rolling HISTORY state
        (kicks/handballs/marks/tackles/clearances/inside_50s/contested/
        uncontested/disposals deques, season/career sums, EWMA) - never to
        the row's own `disposals` target value, which always stays the
        real, actually-recorded count. Exists to test one candidate fix for
        Section 19 (2020's shortened quarters): scaling 2020's history
        contribution up toward a normal-season rate so a 2021 player's
        rolling average isn't dragged down by games played under a
        shorter format, without ever pretending 2020's OWN disposal counts
        (the actual outcomes 2020 predictions are scored against) were
        anything other than what really happened. See
        disposal_analysis.py's compare_2020_handling() for the empirical
        comparison against leaving this unset (the default, no adjustment)."""
        self._team_context = team_context or {}
        self._season_scale_factors = season_scale_factors or {}

    def build(
        self,
        player_rows: list[PlayerGameRow],
        team_rows: list[TeamGameRow] | None = None,
    ) -> list[DisposalFeatureRow]:
        team_rows = team_rows or []
        player_rows_sorted = sorted(player_rows, key=lambda r: (r.scheduled_start, r.match_id, r.player_id))
        team_rows_sorted = sorted(team_rows, key=lambda r: (r.scheduled_start, r.match_id))

        histories: dict[int, _PlayerHistory] = defaultdict(_PlayerHistory)
        team_disposal_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        # disposals_conceded_hist[T] = T's own history of disposals its
        # opponents recorded AGAINST it - i.e. what T "conceded"/allowed.
        disposals_conceded_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        venue_totals: dict[int, list[int]] = defaultdict(list)  # venue_id -> list of per-player-game disposals seen

        # Team-level rolling context is advanced strictly by match order too
        # (grouped by match so both sides of a match snapshot BEFORE either
        # side's result updates state - order within a match must not
        # matter), merged into the player walk below via match_id lookup so
        # both walks share one chronological clock.
        team_ctx_by_match: dict[int, dict[int, tuple[float | None, float | None]]] = defaultdict(dict)
        matches_order: list[int] = []
        rows_by_match: dict[int, list[TeamGameRow]] = defaultdict(list)
        for t in team_rows_sorted:
            if t.match_id not in rows_by_match:
                matches_order.append(t.match_id)
            rows_by_match[t.match_id].append(t)

        for match_id in matches_order:
            pair = rows_by_match[match_id]
            for t in pair:
                team_ctx_by_match[t.match_id][t.team_id] = (
                    _avg(team_disposal_hist[t.team_id]),
                    _avg(disposals_conceded_hist[t.opponent_team_id]) if t.opponent_team_id else None,
                )
            for t in pair:
                if t.disposals is None:
                    continue
                team_disposal_hist[t.team_id].append(t.disposals)
                if t.opponent_team_id is not None:
                    disposals_conceded_hist[t.opponent_team_id].append(t.disposals)

        result: list[DisposalFeatureRow] = []
        # league-average disposals, computed expanding as we go, for venue shrinkage fallback
        league_sum, league_n = 0, 0

        for row in player_rows_sorted:
            hist = histories[row.player_id]
            league_avg = league_sum / league_n if league_n else LEAGUE_AVG_DISPOSALS_FALLBACK

            team_snapshot = team_ctx_by_match.get(row.match_id, {}).get(row.team_id, (None, None))
            team_recent_disposals_avg, opponent_disposals_conceded_avg = team_snapshot

            venue_hist = venue_totals.get(row.venue_id, []) if row.venue_id is not None else []
            venue_env = _shrink(_avg(venue_hist), len(venue_hist), league_avg)

            ctx = self._team_context.get(row.match_id, {}).get(row.team_id, {})

            tog_last3 = _avg(list(hist.tog)[-3:])
            tog_last7_prior = _avg(list(hist.tog)[-10:-3]) if len(hist.tog) > 3 else None
            tog_trend = (tog_last3 - tog_last7_prior) if (tog_last3 is not None and tog_last7_prior is not None) else None

            disposals_last3 = _avg(list(hist.disposals)[-3:])
            disposals_last10 = _avg(list(hist.disposals)[-10:])
            disposal_trend = (
                (disposals_last3 - disposals_last10) if (disposals_last3 is not None and disposals_last10 is not None) else None
            )

            season_list = hist.season_disposals.get(row.season_year, [])
            disposals_season_avg = _avg(season_list) if season_list else None

            features = {
                "disposals_last3_avg": disposals_last3,
                "disposals_last5_avg": _avg(list(hist.disposals)[-5:]),
                "disposals_last10_avg": disposals_last10,
                "disposals_season_avg": disposals_season_avg,
                "disposals_career_avg": (hist.career_disposal_sum / hist.n_games) if hist.n_games else None,
                "disposals_last5_median": _median(list(hist.disposals)[-5:]),
                "disposals_last5_std": _std(list(hist.disposals)[-5:]),
                "disposals_last10_std": _std(list(hist.disposals)),
                "disposals_ewma": hist.ewma_disposals,
                "kicks_last5_avg": _avg(hist.kicks),
                "handballs_last5_avg": _avg(hist.handballs),
                "marks_last5_avg": _avg(hist.marks),
                "tackles_last5_avg": _avg(hist.tackles),
                "clearances_last5_avg": _avg(hist.clearances),
                "inside_50s_last5_avg": _avg(hist.inside_50s),
                "contested_possessions_last5_avg": _avg(hist.contested_possessions),
                "uncontested_possessions_last5_avg": _avg(hist.uncontested_possessions),
                "tog_last3_avg": tog_last3,
                "tog_last5_avg": _avg(list(hist.tog)[-5:]),
                "tog_last10_avg": _avg(hist.tog),
                "disposals_per_tog100_career": (hist.career_disposal_sum / hist.career_tog_sum) if hist.career_tog_sum else None,
                "tog_trend": tog_trend,
                "disposal_trend": disposal_trend,
                "team_recent_disposals_avg": team_recent_disposals_avg,
                "opponent_disposals_conceded_avg": opponent_disposals_conceded_avg,
                "venue_disposals_env": venue_env,
                "team_elo_win_prob": ctx.get("elo_win_prob"),
                "team_expected_score": ctx.get("expected_score"),
                "opponent_expected_score": ctx.get("opponent_expected_score"),
                "expected_margin": ctx.get("expected_margin"),
                "is_home": 1.0 if row.is_home else 0.0,
            }

            result.append(
                DisposalFeatureRow(
                    player_id=row.player_id,
                    match_id=row.match_id,
                    team_id=row.team_id,
                    opponent_team_id=row.opponent_team_id,
                    season_year=row.season_year,
                    round_number=row.round_number,
                    is_final=row.is_final,
                    scheduled_start=row.scheduled_start,
                    disposals=row.disposals,
                    games_of_history=hist.n_games,
                    features=features,
                )
            )

            # --- update state with this row's own actual result, AFTER building its features ---
            # scale_factor only affects what feeds future rows' HISTORY -
            # row.disposals itself (the target already stored above in
            # `result`) is never touched, so a scaled 2020 row is still
            # scored against its real, actually-recorded disposal count.
            scale_factor = self._season_scale_factors.get(row.season_year, 1.0)
            scaled_disposals = row.disposals * scale_factor

            hist.disposals.append(scaled_disposals)
            hist.kicks.append(row.kicks * scale_factor if row.kicks is not None else None)
            hist.handballs.append(row.handballs * scale_factor if row.handballs is not None else None)
            hist.marks.append(row.marks * scale_factor if row.marks is not None else None)
            hist.tackles.append(row.tackles * scale_factor if row.tackles is not None else None)
            hist.clearances.append(row.clearances * scale_factor if row.clearances is not None else None)
            hist.inside_50s.append(row.inside_50s * scale_factor if row.inside_50s is not None else None)
            hist.contested_possessions.append(row.contested_possessions * scale_factor if row.contested_possessions is not None else None)
            hist.uncontested_possessions.append(
                row.uncontested_possessions * scale_factor if row.uncontested_possessions is not None else None
            )
            if row.time_on_ground_pct is not None:
                hist.tog.append(row.time_on_ground_pct)
                hist.career_tog_sum += row.time_on_ground_pct / 100.0
            hist.season_disposals[row.season_year].append(scaled_disposals)
            hist.career_disposal_sum += scaled_disposals
            hist.n_games += 1
            hist.ewma_disposals = (
                scaled_disposals
                if hist.ewma_disposals is None
                else EWMA_ALPHA * scaled_disposals + (1 - EWMA_ALPHA) * hist.ewma_disposals
            )

            if row.venue_id is not None:
                venue_totals[row.venue_id].append(row.disposals)
            league_sum += row.disposals
            league_n += 1

        return result
