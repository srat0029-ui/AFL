"""Research script (Role/Archetype Research stage): investigates whether
point-in-time-safe player role/usage-context signals and an inferred
statistical archetype improve disposal/goal pricing beyond the current
promoted Huber disposal / hurdle goal models.

READ-ONLY RESEARCH — never touches a promoted model row, never writes to the
DB, never modifies disposal_features.py/goal_features.py. Reuses the exact
existing loaders, leakage-safe feature builders, chronological tune/eval
split (EVALUATION_START_YEAR=2019), and evaluation harness for both markets
unchanged; the only new code is (1) a point-in-time role/usage feature
builder, (2) archetype clustering fit on tune-period data only, and (3)
challenger feature-set variants passed into the existing fit_huber /
fit_hurdle_model entry points via run_candidate_models/run_goal_candidate_models.

Design (deliberately not a grid search — one clustering config, two
challenger variants per market):
  - Archetype: KMeans, k=7 (matching the 7 example AFL role archetypes in
    the brief: high-volume mid, inside mid, outside/wing, rebounding
    defender, key forward, small/medium forward, ruck), fit on standardized,
    median-imputed TUNE-period (pre-2019) recent-role-profile vectors only.
    Every row (tune or eval) is assigned by nearest centroid using ONLY that
    row's own prior-games profile — no leakage.
  - Role/usage features added are limited to signals genuinely NOT already
    in the promoted feature set (team-relative disposal share, kick/handball
    share, contested/uncontested MIX ratio, scoring involvement for the
    disposal model, and a role_change_score = distance between a player's
    last-5-game profile and their prior longer-run profile). Redundant raw
    counts already in the promoted set (marks/clearances/inside_50s last-5
    averages) are deliberately NOT re-added.
  - Challenger A ("role"): promoted/current feature set + role/usage
    features + role_change_score, no archetype.
  - Challenger B ("role_archetype"): Challenger A + one-hot archetype dummies
    (6 dummies, one archetype dropped as reference).

Run: python -m scripts.role_archetype_research
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Match, MatchStatus, PlayerMatchStat, Sport

from app.player_modelling.disposal_backtest import EVALUATION_START_YEAR, build_dataset, run_candidate_models
from app.player_modelling.disposal_data import load_team_game_rows
from app.player_modelling.disposal_evaluation import CALIBRATION_THRESHOLDS, evaluate_model
from app.player_modelling.disposal_features import PROMOTED_DISPOSAL_FEATURE_NAMES

from app.player_modelling.goal_backtest import build_goal_dataset, run_goal_candidate_models
from app.player_modelling.goal_evaluation import THRESHOLDS as GOAL_THRESHOLDS, evaluate_goal_model
from app.player_modelling.goal_features import PLAYER_FEATURE_NAMES as GOAL_PLAYER_FEATURE_NAMES

N_ARCHETYPES = 7
ARCHETYPE_MIN_GAMES = 5  # below this, a player's role profile is too noisy to cluster meaningfully
ROLE_CHANGE_MIN_GAMES = 10  # need a real "recent" (5) vs "prior" window to compute a change score at all
ROLE_DIMS = ("tog", "disposal_share", "kick_share", "i50", "clearances", "marks", "contested_share", "scoring")
RANDOM_STATE = 42


# ---------------------------------------------------------------------------
# Raw per-player-match rows (superset of disposal_data/goal_data's fields —
# everything needed to build role/usage profiles) and team-disposal lookup.
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


# ---------------------------------------------------------------------------
# Archetype clustering (fit on tune-period rows only) + role-change score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchetypeModel:
    imputer: SimpleImputer
    scaler: StandardScaler
    kmeans: KMeans


def fit_archetype_model(role_rows: list[RoleRow]) -> ArchetypeModel:
    tune_rows = [r for r in role_rows if r.season_year < EVALUATION_START_YEAR and r.games_of_history >= ARCHETYPE_MIN_GAMES]
    X = np.array([[r.recent[d] for d in ROLE_DIMS] for r in tune_rows], dtype=float)  # numpy casts None -> nan under dtype=float
    imputer = SimpleImputer(strategy="median").fit(X)
    scaler = StandardScaler().fit(imputer.transform(X))
    kmeans = KMeans(n_clusters=N_ARCHETYPES, random_state=RANDOM_STATE, n_init=10).fit(scaler.transform(imputer.transform(X)))
    return ArchetypeModel(imputer=imputer, scaler=scaler, kmeans=kmeans)


def _vec(profile: dict[str, float | None]) -> np.ndarray:
    return np.array([[profile[d] if profile[d] is not None else np.nan for d in ROLE_DIMS]], dtype=float)


def role_features_for(role_row: RoleRow, arch: ArchetypeModel) -> dict[str, float | None]:
    features: dict[str, float | None] = {
        "role_tog_recent": role_row.recent["tog"],
        "role_disposal_share_recent": role_row.recent["disposal_share"],
        "role_kick_share_recent": role_row.recent["kick_share"],
        "role_contested_share_recent": role_row.recent["contested_share"],
        "role_scoring_recent": role_row.recent["scoring"],
    }

    if role_row.games_of_history >= ARCHETYPE_MIN_GAMES:
        recent_scaled = arch.scaler.transform(arch.imputer.transform(_vec(role_row.recent)))
        archetype_id = int(arch.kmeans.predict(recent_scaled)[0])
    else:
        archetype_id = None
    for i in range(N_ARCHETYPES - 1):  # drop last archetype as reference category
        features[f"archetype_{i}"] = (1.0 if archetype_id == i else 0.0) if archetype_id is not None else None
    features["_archetype_id"] = float(archetype_id) if archetype_id is not None else None  # diagnostics only, not fed to models

    if role_row.games_of_history >= ROLE_CHANGE_MIN_GAMES and all(v is not None for v in role_row.longterm.values()) and all(v is not None for v in role_row.recent.values()):
        recent_scaled = arch.scaler.transform(arch.imputer.transform(_vec(role_row.recent)))
        longterm_scaled = arch.scaler.transform(arch.imputer.transform(_vec(role_row.longterm)))
        features["role_change_score"] = float(np.linalg.norm(recent_scaled - longterm_scaled))
    else:
        features["role_change_score"] = None
    return features


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DISPOSAL_ROLE_FEATURES = PROMOTED_DISPOSAL_FEATURE_NAMES + (
    "role_tog_recent", "role_disposal_share_recent", "role_kick_share_recent", "role_contested_share_recent", "role_scoring_recent", "role_change_score",
)
DISPOSAL_ARCHETYPE_DUMMIES = tuple(f"archetype_{i}" for i in range(N_ARCHETYPES - 1))
DISPOSAL_ROLE_ARCHETYPE_FEATURES = DISPOSAL_ROLE_FEATURES + DISPOSAL_ARCHETYPE_DUMMIES

GOAL_ROLE_FEATURES = GOAL_PLAYER_FEATURE_NAMES + (
    "role_disposal_share_recent", "role_kick_share_recent", "role_contested_share_recent", "role_change_score",
)
GOAL_ROLE_ARCHETYPE_FEATURES = GOAL_ROLE_FEATURES + DISPOSAL_ARCHETYPE_DUMMIES


def main() -> None:
    db = SessionLocal()
    try:
        split = build_dataset(db)
        gsplit = build_goal_dataset(db)
        raw_rows = load_raw_player_rows(db)
        team_rows = load_team_game_rows(db)
    finally:
        db.close()

    team_disposals_by_match = {(t.team_id, t.match_id): t.disposals for t in team_rows if t.disposals is not None}
    role_rows = build_role_rows(raw_rows, team_disposals_by_match)
    print(f"tune rows: {len(split.tune_rows):,}  eval rows: {len(split.eval_rows):,}  role rows built: {len(role_rows):,}\n")

    arch = fit_archetype_model(role_rows)
    role_by_key: dict[tuple[int, int], dict] = {}
    for r in role_rows:
        role_by_key[(r.player_id, r.match_id)] = role_features_for(r, arch)

    n_matched = 0
    for r in split.all_rows:
        feats = role_by_key.get((r.player_id, r.match_id))
        if feats:
            r.features.update(feats)
            n_matched += 1
    for r in gsplit.all_rows:
        feats = role_by_key.get((r.player_id, r.match_id))
        if feats:
            r.features.update(feats)
    print(f"role features merged onto {n_matched:,}/{len(split.all_rows):,} disposal rows\n")

    # ============================================================
    # 1) Archetype stability / interpretability (descriptive only)
    # ============================================================
    print("=" * 70)
    print("STEP 1: Archetype stability & interpretability")
    print("=" * 70)
    eval_role_by_key = {(r.player_id, r.match_id): r for r in role_rows if r.season_year >= EVALUATION_START_YEAR}
    centroid_sums: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    centroid_counts: dict[int, int] = defaultdict(int)
    player_archetypes: dict[int, list[int]] = defaultdict(list)
    for r in role_rows:
        if r.season_year < EVALUATION_START_YEAR or r.games_of_history < ARCHETYPE_MIN_GAMES:
            continue
        feats = role_by_key[(r.player_id, r.match_id)]
        aid = feats["_archetype_id"]
        if aid is None:
            continue
        aid = int(aid)
        player_archetypes[r.player_id].append(aid)
        centroid_counts[aid] += 1
        for d in ROLE_DIMS:
            v = r.recent[d]
            if v is not None:
                centroid_sums[aid][d] += v

    print(f"{'archetype':>10} {'n_rows':>8} " + " ".join(f"{d:>16}" for d in ROLE_DIMS))
    for aid in sorted(centroid_counts):
        n = centroid_counts[aid]
        line = f"{aid:>10} {n:>8} "
        line += " ".join(f"{(centroid_sums[aid][d] / n if n else float('nan')):>16.3f}" for d in ROLE_DIMS)
        print(line)

    modal_shares = []
    for player_id, labels in player_archetypes.items():
        if len(labels) < ARCHETYPE_MIN_GAMES:
            continue
        counts = defaultdict(int)
        for lab in labels:
            counts[lab] += 1
        modal_shares.append(max(counts.values()) / len(labels))
    if modal_shares:
        print(f"\nPer-player modal-archetype share (eval period, players w/ >={ARCHETYPE_MIN_GAMES} eval games, n={len(modal_shares)}):")
        print(f"  mean={np.mean(modal_shares):.3f}  median={np.median(modal_shares):.3f}  pct>=0.6={np.mean(np.array(modal_shares) >= 0.6):.1%}  pct>=0.8={np.mean(np.array(modal_shares) >= 0.8):.1%}")

    # ============================================================
    # 2) Disposal challengers
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 2: Disposal — promoted Huber vs role-augmented challengers")
    print("=" * 70)
    disposal_current = run_candidate_models(split, feature_names=PROMOTED_DISPOSAL_FEATURE_NAMES, model_names=("huber",))["huber"]
    disposal_role = run_candidate_models(split, feature_names=DISPOSAL_ROLE_FEATURES, model_names=("huber",))["huber"]
    disposal_role_arch = run_candidate_models(split, feature_names=DISPOSAL_ROLE_ARCHETYPE_FEATURES, model_names=("huber",))["huber"]
    disposal_models = {"current_huber": disposal_current, "role_huber": disposal_role, "role_archetype_huber": disposal_role_arch}

    print(f"{'model':>22} {'MAE':>7} {'RMSE':>7} {'bias':>8}", end="")
    for t in CALIBRATION_THRESHOLDS:
        print(f"  ECE@{t:<3}", end="")
    print()
    for name, preds in disposal_models.items():
        ev = evaluate_model(name, preds)
        line = f"{name:>22} {ev.point.mae:>7.3f} {ev.point.rmse:>7.3f} {ev.point.bias:>+8.3f}"
        for t in CALIBRATION_THRESHOLDS:
            line += f"  {ev.thresholds[t].ece:.4f}" if ev.thresholds[t].ece is not None else "     n/a"
        print(line)

    disposal_rows_by_key = {(r.player_id, r.match_id): r for r in split.eval_rows}
    print("\nBias by career-average volume bucket:")
    edges, labels = [0, 15, 22, 25, 28, 100], ["<15", "15-22", "22-25", "25-28", "28+"]
    for name, preds in disposal_models.items():
        groups = defaultdict(list)
        for p in preds:
            r = disposal_rows_by_key[(p.player_id, p.match_id)]
            v = r.features.get("disposals_career_avg") or 0
            for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
                if lo <= v < hi:
                    groups[lab].append(p.predicted_mean - p.actual)
                    break
        line = f"  {name:>22}: " + "  ".join(f"{lab}={np.mean(groups[lab]):+.3f}(n={len(groups[lab])})" for lab in labels if groups[lab])
        print(line)

    # role-change bucket: does the current model fail more when a player's
    # recent profile diverges from their longer-run baseline?
    print("\nRole-change bucket comparison (role_change_score available rows only):")
    rcs_values = sorted(v for v in (disposal_rows_by_key[k].features.get("role_change_score") for k in disposal_rows_by_key) if v is not None)
    if rcs_values:
        median_rcs = np.median(rcs_values)
        for name, preds in disposal_models.items():
            low, high = [], []
            for p in preds:
                rcs = disposal_rows_by_key[(p.player_id, p.match_id)].features.get("role_change_score")
                if rcs is None:
                    continue
                (low if rcs < median_rcs else high).append(p.predicted_mean - p.actual)
            if low and high:
                print(f"  {name:>22}: role_stable(n={len(low)}) MAE-bias={np.mean(np.abs(low)):.3f}/{np.mean(low):+.3f}   role_changed(n={len(high)}) MAE-bias={np.mean(np.abs(high)):.3f}/{np.mean(high):+.3f}")

    # ============================================================
    # 3) Goal challengers
    # ============================================================
    print("\n" + "=" * 70)
    print("STEP 3: Goals — promoted hurdle vs role-augmented challengers")
    print("=" * 70)
    goal_current = run_goal_candidate_models(gsplit, feature_names=GOAL_PLAYER_FEATURE_NAMES, model_names=("hurdle",))["hurdle"]
    goal_role = run_goal_candidate_models(gsplit, feature_names=GOAL_ROLE_FEATURES, model_names=("hurdle",))["hurdle"]
    goal_role_arch = run_goal_candidate_models(gsplit, feature_names=GOAL_ROLE_ARCHETYPE_FEATURES, model_names=("hurdle",))["hurdle"]
    goal_models_ = {"current_hurdle": goal_current, "role_hurdle": goal_role, "role_archetype_hurdle": goal_role_arch}

    print(f"{'model':>22} {'MAE':>7} {'RMSE':>7} {'bias':>8}", end="")
    for t in (1, 2, 3):
        print(f"  ECE@{t:<3}", end="")
    print()
    for name, preds in goal_models_.items():
        ev = evaluate_goal_model(name, preds)
        line = f"{name:>22} {ev.point.mae:>7.3f} {ev.point.rmse:>7.3f} {ev.point.bias:>+8.3f}"
        for t in (1, 2, 3):
            line += f"  {ev.thresholds[t].ece:.4f}" if ev.thresholds[t].ece is not None else "     n/a"
        print(line)

    print("\nDone. Research only — no model promoted, no DB writes.")


if __name__ == "__main__":
    main()
