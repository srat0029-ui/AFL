"""Production usage-regime detector — Section 1 of the Usage-Change
Production Integration stage: identifies when a player's RECENT statistical
usage profile materially diverges from their own longer-run baseline.

This is the EXACT method validated in scripts/role_archetype_research.py
(the leakage-safe role/usage profile builder) and
scripts/usage_regime_change_research.py (the change-score/cutoff design),
relocated here as the single production home so both those research scripts
and the live pipeline share one implementation — nothing about the
algorithm is reinvented for production.

Design (unchanged from the validated research):
  - 8 point-in-time dims per player-game: TOG, disposal share of team
    total, kick/handball share, inside-50 involvement, clearances, marks,
    contested-possession share, scoring involvement (goals+behinds) — each
    a rolling average built from that player's own STRICTLY PRIOR games
    only (see build_role_rows's leakage discipline, identical to
    disposal_features.py's).
  - change_score = standardized Euclidean distance between a player's
    last-5-game profile ("recent") and their prior (games -40..-6) profile
    ("longterm"), using an imputer+scaler fit ONCE on TUNE-period
    (pre-EVALUATION_START_YEAR) historical rows — never refit against live
    data, so the threshold is the same one the research evaluated.
  - stable/changed split at the TUNE-period median change_score — a fixed,
    deployable cutoff decided before ever looking at eval or live data.

This module produces INFORMATIONAL METADATA ONLY: usage_regime and
usage_change_score are never fed into a point prediction or a probability
(see usage_regime_change_research.py's findings — the point estimate/
calibration evidence did not support that). Consumers may only use this to
annotate/flag output, never to alter a model's numeric belief.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchStatus, PlayerMatchStat, Sport
from app.player_modelling.disposal_backtest import EVALUATION_START_YEAR
from app.player_modelling.disposal_data import TeamGameRow, load_team_game_rows
from app.player_modelling.request_cache import cached_model_fit
from app.player_modelling.upcoming_features import ExpectedPlayer, UpcomingMatchTeams

ROLE_DIMS: tuple[str, ...] = ("tog", "disposal_share", "kick_share", "i50", "clearances", "marks", "contested_share", "scoring")
ROLE_CHANGE_MIN_GAMES = 10  # need a real recent(5) vs prior(-40..-6) window to compute a score at all
STABLE = "stable"
CHANGED = "changed"
INSUFFICIENT_HISTORY = "insufficient_history"


# ---------------------------------------------------------------------------
# Raw per-player-match rows (superset of disposal_data/goal_data's fields)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPlayerRow:
    player_id: int
    match_id: int
    team_id: int
    season_year: int
    scheduled_start: datetime
    disposals: int | None
    kicks: int | None
    marks: int | None
    clearances: int | None
    inside_50s: int | None
    contested_possessions: int | None
    uncontested_possessions: int | None
    time_on_ground_pct: int | None
    goals: int | None
    behinds: int | None


def load_raw_player_rows(db: Session, sport_code: str = "AFL", source: str = "afltables") -> list[RawPlayerRow]:
    rows = db.execute(
        select(PlayerMatchStat, Match)
        .join(Match, Match.id == PlayerMatchStat.match_id)
        .join(Sport, Sport.id == Match.sport_id)
        .where(
            Sport.code == sport_code, Match.status == MatchStatus.COMPLETED,
            PlayerMatchStat.source == source, PlayerMatchStat.disposals.is_not(None),
        )
        .order_by(Match.scheduled_start, Match.id)
    ).all()
    return [
        RawPlayerRow(
            player_id=s.player_id, match_id=m.id, team_id=s.team_id, season_year=m.season.year,
            scheduled_start=m.scheduled_start, disposals=s.disposals, kicks=s.kicks, marks=s.marks,
            clearances=s.clearances, inside_50s=s.inside_50s, contested_possessions=s.contested_possessions,
            uncontested_possessions=s.uncontested_possessions, time_on_ground_pct=s.time_on_ground_pct,
            goals=s.goals, behinds=s.behinds,
        )
        for s, m in rows
    ]


# ---------------------------------------------------------------------------
# Point-in-time role/usage profile builder — same leakage discipline as
# disposal_features.py: a row's own stats are folded into history only AFTER
# that row's profile has been produced.
# ---------------------------------------------------------------------------


@dataclass
class _RoleHistory:
    tog: deque = field(default_factory=lambda: deque(maxlen=40))
    disposal_share: deque = field(default_factory=lambda: deque(maxlen=40))
    kick_share: deque = field(default_factory=lambda: deque(maxlen=40))
    i50: deque = field(default_factory=lambda: deque(maxlen=40))
    clearances: deque = field(default_factory=lambda: deque(maxlen=40))
    marks: deque = field(default_factory=lambda: deque(maxlen=40))
    contested_share: deque = field(default_factory=lambda: deque(maxlen=40))
    scoring: deque = field(default_factory=lambda: deque(maxlen=40))
    n_games: int = 0


@dataclass(frozen=True)
class RoleRow:
    player_id: int
    match_id: int
    season_year: int
    games_of_history: int
    recent: dict[str, float | None]  # last-5-game window
    longterm: dict[str, float | None]  # the window strictly before that (games -40..-6)


def _avg(vals) -> float | None:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def build_role_rows(player_rows: list[RawPlayerRow], team_disposals_by_match: dict[tuple[int, int], float]) -> list[RoleRow]:
    rows_sorted = sorted(player_rows, key=lambda r: (r.scheduled_start, r.match_id, r.player_id))
    histories: dict[int, _RoleHistory] = defaultdict(_RoleHistory)
    result = []
    for row in rows_sorted:
        hist = histories[row.player_id]
        recent = {d: _avg(list(getattr(hist, d))[-5:]) for d in ROLE_DIMS}
        longterm = {d: _avg(list(getattr(hist, d))[-40:-5]) if hist.n_games >= ROLE_CHANGE_MIN_GAMES else None for d in ROLE_DIMS}
        result.append(RoleRow(player_id=row.player_id, match_id=row.match_id, season_year=row.season_year, games_of_history=hist.n_games, recent=recent, longterm=longterm))

        team_total = team_disposals_by_match.get((row.team_id, row.match_id))
        hist.tog.append(row.time_on_ground_pct)
        hist.disposal_share.append(row.disposals / team_total if (row.disposals is not None and team_total) else None)
        hist.kick_share.append(row.kicks / row.disposals if (row.kicks is not None and row.disposals) else None)
        hist.i50.append(row.inside_50s)
        hist.clearances.append(row.clearances)
        hist.marks.append(row.marks)
        co, un = row.contested_possessions, row.uncontested_possessions
        hist.contested_share.append(co / (co + un) if (co is not None and un is not None and (co + un) > 0) else None)
        hist.scoring.append((row.goals or 0) + (row.behinds or 0) if row.goals is not None else None)
        hist.n_games += 1
    return result


def load_role_rows(db: Session) -> list[RoleRow]:
    raw_rows = load_raw_player_rows(db)
    team_rows: list[TeamGameRow] = load_team_game_rows(db)
    team_disposals_by_match = {(t.team_id, t.match_id): t.disposals for t in team_rows if t.disposals is not None}
    return build_role_rows(raw_rows, team_disposals_by_match)


# ---------------------------------------------------------------------------
# The fitted detector: imputer + scaler (fit on tune-period rows only) + the
# tune-derived stable/changed cutoff. Deterministic given the same
# historical data, so it's safe to fit once and cache (see usage_regime_live.py).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRegimeModel:
    imputer: SimpleImputer | None
    scaler: StandardScaler | None
    cutoff: float  # NaN means "no usable reference data" - see fit_usage_regime_model


def _vec(profile: dict) -> np.ndarray:
    return np.array([[profile[d] if profile[d] is not None else np.nan for d in ROLE_DIMS]], dtype=float)


def fit_usage_regime_model(role_rows: list[RoleRow]) -> UsageRegimeModel:
    """Prefers the strict, validated pre-EVALUATION_START_YEAR tune split
    (the same one the research scripts evaluated against). Falls back to
    ANY historical rows with enough games if that split is empty - a
    real possibility for a small/young dataset (a fresh deployment with no
    pre-2019-equivalent history yet, or a test database) - rather than
    crashing the live pipeline entirely. If literally no row anywhere has
    enough history, returns an empty model: every row is then classified
    insufficient_history (see usage_regime_for), never a false "stable"."""
    tune_rows = [r for r in role_rows if r.season_year < EVALUATION_START_YEAR and r.games_of_history >= ROLE_CHANGE_MIN_GAMES]
    if not tune_rows:
        tune_rows = [r for r in role_rows if r.games_of_history >= ROLE_CHANGE_MIN_GAMES]
    if not tune_rows:
        return UsageRegimeModel(imputer=None, scaler=None, cutoff=float("nan"))

    X_tune = np.array([[r.recent[d] for d in ROLE_DIMS] for r in tune_rows], dtype=float)
    imputer = SimpleImputer(strategy="median").fit(X_tune)
    scaler = StandardScaler().fit(imputer.transform(X_tune))

    scores = []
    for r in tune_rows:
        if any(v is None for v in r.longterm.values()):
            continue
        recent_scaled = scaler.transform(imputer.transform(_vec(r.recent)))
        longterm_scaled = scaler.transform(imputer.transform(_vec(r.longterm)))
        scores.append(float(np.linalg.norm(recent_scaled - longterm_scaled)))
    cutoff = float(np.median(scores)) if scores else float("nan")
    return UsageRegimeModel(imputer=imputer, scaler=scaler, cutoff=cutoff)


@dataclass(frozen=True)
class UsageRegimeResult:
    usage_regime: str  # "stable" | "changed" | "insufficient_history"
    usage_change_score: float | None
    threshold_used: float
    changed_dimensions: list[str]  # the dims contributing most to the score, only populated when changed


def usage_regime_for(role_row: RoleRow, model: UsageRegimeModel) -> UsageRegimeResult:
    """A row needs enough games for a real recent-vs-longterm window
    (ROLE_CHANGE_MIN_GAMES) and at least SOME real signal in each window -
    it does NOT require every one of the 8 dims to be individually present.
    Team-level stat ingestion routinely lags player-level stats for the
    most recent rounds in this dataset (team-relative dims like
    disposal_share/contested_share can be null for otherwise
    well-established players' latest games) - requiring every dim would
    make "changed" nearly unreachable in the live case. Missing individual
    dims are median-imputed via model.imputer, the same tolerance every
    other model in this codebase (Ridge/Huber/hurdle) already applies to
    partially-missing features, rather than refusing to classify at all."""
    if (
        model.imputer is None
        or role_row.games_of_history < ROLE_CHANGE_MIN_GAMES
        or all(v is None for v in role_row.longterm.values())
        or all(v is None for v in role_row.recent.values())
    ):
        return UsageRegimeResult(usage_regime=INSUFFICIENT_HISTORY, usage_change_score=None, threshold_used=model.cutoff, changed_dimensions=[])

    recent_scaled = model.scaler.transform(model.imputer.transform(_vec(role_row.recent)))[0]
    longterm_scaled = model.scaler.transform(model.imputer.transform(_vec(role_row.longterm)))[0]
    per_dim_delta = recent_scaled - longterm_scaled
    score = float(np.linalg.norm(per_dim_delta))
    regime = CHANGED if score >= model.cutoff else STABLE

    changed_dims: list[str] = []
    if regime == CHANGED:
        ranked = sorted(zip(ROLE_DIMS, per_dim_delta), key=lambda kv: abs(kv[1]), reverse=True)
        changed_dims = [dim for dim, _ in ranked[:2]]

    return UsageRegimeResult(usage_regime=regime, usage_change_score=score, threshold_used=model.cutoff, changed_dimensions=changed_dims)


# ---------------------------------------------------------------------------
# B2B model-risk metadata (Usage-Change Production Integration stage, item
# 6): a small, generalised, structured flag any pricing/insight response
# can carry — code+description, not free text, so another trading/data
# system can branch on `code` programmatically. Only flags backed by actual
# held-out evidence are ever added (see scripts/usage_regime_change_research.py)
# — no speculative flags. Kept here (not in app/pricing/player_pricing.py)
# so both the B2B pricing API and the internal product's prop-insight/
# best-opportunities pipeline can import it without a circular dependency.
# ---------------------------------------------------------------------------

USAGE_REGIME_CHANGE_FLAG = "RECENT_USAGE_REGIME_CHANGE"


@dataclass(frozen=True)
class ModelRiskFlag:
    code: str
    description: str


def goal_usage_risk_flags(usage_regime: str | None) -> list[ModelRiskFlag]:
    """Goal-only: usage_regime_change_research.py found an ~11% higher
    point-error for goals in the "changed" regime, with no held-out support
    for adjusting the probability, confidence tier, or interval — so this
    flag is informational risk metadata, never a numeric adjustment. The
    disposal market's equivalent effect (~1.7%) did not meet the bar for a
    flag (see that research's report, item 3)."""
    if usage_regime != CHANGED:
        return []
    return [
        ModelRiskFlag(
            code=USAGE_REGIME_CHANGE_FLAG,
            description=(
                "Recent usage profile materially differs from the player's established baseline. "
                "Historically, goal point-error was approximately 11% higher in this state."
            ),
        )
    ]


def cached_usage_regime_model(db: Session) -> UsageRegimeModel:
    """Fit is deterministic given historical data alone (tune-period rows,
    pre-EVALUATION_START_YEAR — never affected by live results), so it's
    cached exactly like live_engine.py's live_disposal_model/live_goal_model
    fits rather than refit on every pricing/live-cycle call."""
    return cached_model_fit(db, ("usage_regime_model",), lambda: fit_usage_regime_model(load_role_rows(db)))


def build_upcoming_role_rows(
    db: Session, upcoming_matches: list[UpcomingMatchTeams], expected_players: list[ExpectedPlayer]
) -> dict[tuple[int, int], RoleRow]:
    """The "current player state" counterpart to build_role_rows — mirrors
    upcoming_features.py's synthetic-row trick exactly: one placeholder row
    per expected player, chronologically after every real completed match,
    so its OWN recent/longterm profile reflects real prior games only (the
    placeholder's own stats are never real and are discarded after its
    profile is read off)."""
    player_rows = list(load_raw_player_rows(db))
    team_rows = list(load_team_game_rows(db))
    team_disposals_by_match = {(t.team_id, t.match_id): t.disposals for t in team_rows if t.disposals is not None}
    match_by_id = {m.match_id: m for m in upcoming_matches}

    synthetic_keys: set[tuple[int, int]] = set()
    for ep in expected_players:
        m = match_by_id[ep.match_id]
        player_rows.append(
            RawPlayerRow(
                player_id=ep.player_id, match_id=ep.match_id, team_id=ep.team_id, season_year=m.season_year,
                scheduled_start=m.scheduled_start, disposals=0, kicks=None, marks=None, clearances=None,
                inside_50s=None, contested_possessions=None, uncontested_possessions=None,
                time_on_ground_pct=None, goals=0, behinds=None,
            )
        )
        synthetic_keys.add((ep.player_id, ep.match_id))

    all_rows = build_role_rows(player_rows, team_disposals_by_match)
    return {(r.player_id, r.match_id): r for r in all_rows if (r.player_id, r.match_id) in synthetic_keys}


def compute_upcoming_usage_regimes(
    db: Session, upcoming_matches: list[UpcomingMatchTeams], expected_players: list[ExpectedPlayer]
) -> dict[tuple[int, int], UsageRegimeResult]:
    model = cached_usage_regime_model(db)
    role_by_key = build_upcoming_role_rows(db, upcoming_matches, expected_players)
    return {key: usage_regime_for(role_row, model) for key, role_row in role_by_key.items()}
