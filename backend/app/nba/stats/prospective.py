"""Freeze Stat Model V1 predictions before tip-off, and score the frozen rows.

The freeze step reads a V1.5 snapshot that has already been frozen and writes
nba_prop_projections rows beside it. It never writes to, or changes, a V1.5
table. A row's expected minutes are the snapshot row's own frozen value, and
its information cutoff is the snapshot's.

A prediction is written only while the snapshot's own timing window is open
(target cutoff + tolerance, the rule the snapshot itself obeys) and before
tip-off. After that the gap stays a gap: nothing is back-filled. Each snapshot
is one transaction, so a failure leaves no partial set and can be retried while
the window is open.

Parameters come from serving_v1.json, copied unchanged from the 2025-26 fold of
the recorded offline test. Nothing is fitted here.

Validation status travels with every row. The points distribution failed its
offline rule, so its `distribution_kind` says so.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import InformationLeakError, ensure_utc
from app.models.nba import NbaGame, NbaOutlookSnapshot, NbaPlayerGameLog, NbaPropProjection, NbaRotationPrediction
from app.models.nba.outlook import SNAPSHOT_PARTIAL, SNAPSHOT_PRODUCED, SNAPSHOT_STALE
from app.nba.asof import GAME_RESULT_AVAILABILITY_LAG
from app.nba.markets import NbaPropMarket
from app.nba.minutes.data import load_games, to_utc_naive
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON
from app.nba.outlooks.snapshots import parse_specs
from app.nba.projection import ProjectionOutput
from app.nba.prospective import record_projection
from app.nba.stats import models as sm
from app.nba.stats.features import STATS, add_stat_history

PARAMETERS_PATH = Path(__file__).resolve().parent / "serving_v1.json"
# V1.5 is evidence-blind, so its later labels differ little; two labels keep the table small.
FREEZE_LABELS = ("T-4h", "T-1h")
SNAPSHOT_STATUSES_WITH_ROWS = (SNAPSHOT_PRODUCED, SNAPSHOT_STALE, SNAPSHOT_PARTIAL)
MARKETS = {"points": NbaPropMarket.POINTS, "rebounds": NbaPropMarket.REBOUNDS, "assists": NbaPropMarket.ASSISTS}

# From docs/NBA_STAT_MODEL_V1_RESULTS.md. Changing a status means a new model version.
DISTRIBUTION_KINDS = {"points": "nb_bucket_uncalibrated", "rebounds": "nb_bucket", "assists": "nb_bucket"}
VALIDATION = {
    "points": {"mean": "passed_offline", "distribution": "failed_offline", "calibrated": False},
    "rebounds": {"mean": "passed_offline", "distribution": "passed_offline", "calibrated": True},
    "assists": {"mean": "passed_offline", "distribution": "passed_offline", "calibrated": True},
}


def load_parameters() -> tuple[dict, str]:
    raw = PARAMETERS_PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class NbBucketDistribution:
    """A negative binomial with the alpha of its predicted-mean bucket."""

    mu: float
    alpha: float

    def mean(self) -> float:
        return self.mu

    def prob_over(self, line: float) -> float:
        return float(1.0 - sm.cdf(np.floor(line), np.array([self.mu]), np.array([self.alpha]))[0])

    def prob_under(self, line: float) -> float:
        return float(sm.cdf(np.ceil(line) - 1.0, np.array([self.mu]), np.array([self.alpha]))[0])


@dataclass
class FreezeResult:
    now: datetime
    frozen: list[dict] = field(default_factory=list)
    already_frozen: int = 0
    window_closed: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        rows = sum(item["rows"] for item in self.frozen)
        lines = [f"now {self.now.isoformat()}  frozen snapshots {len(self.frozen)} ({rows} rows)  already frozen {self.already_frozen}  window closed {len(self.window_closed)}  failed {len(self.failed)}"]
        lines += [f"  snapshot {i['snapshot_id']} game {i['game_id']} {i['label']}: {i['rows']} rows, {i['players']} players, no minutes {i['no_minutes']}" for i in self.frozen]
        lines += [f"  snapshot {i['snapshot_id']} game {i['game_id']} {i['label']}: window closed, not back-filled" for i in self.window_closed]
        lines += [f"  snapshot {i['snapshot_id']} game {i['game_id']} {i['label']}: FAILED {i['error']}" for i in self.failed]
        return "\n".join(lines)


def _load_known_logs(db: Session, player_ids: list[int], known_by: datetime) -> pd.DataFrame:
    """The players' box scores that this system held by `known_by`."""
    stmt = select(
        NbaPlayerGameLog.player_id, NbaPlayerGameLog.game_id, NbaPlayerGameLog.did_not_play, NbaPlayerGameLog.minutes,
        NbaPlayerGameLog.points, NbaPlayerGameLog.rebounds, NbaPlayerGameLog.assists, NbaPlayerGameLog.recorded_at,
    ).where(NbaPlayerGameLog.player_id.in_(player_ids), NbaPlayerGameLog.recorded_at <= known_by)
    logs = pd.DataFrame(db.execute(stmt).all(), columns=["player_id", "game_id", "did_not_play", "minutes", *STATS, "recorded_at"])
    logs["did_not_play"] = logs["did_not_play"].astype(bool)
    for col in ("minutes", *STATS):
        logs[col] = logs[col].astype("float64")
    logs["recorded_at"] = to_utc_naive(logs["recorded_at"])
    return logs


def predict_rows(rows: pd.DataFrame, logs: pd.DataFrame, games: pd.DataFrame, parameters: dict) -> pd.DataFrame:
    """Stat V1 and every baseline for one snapshot's rows (player_id, game_id,
    team_id, season_start_year, v1_minutes, t_cut). Fits nothing."""
    frame = add_stat_history(rows.reset_index(drop=True), logs, games)
    for stat in STATS:
        p = parameters["stats"][stat]
        frame[f"{stat}_rate"] = sm.shrunk_rate(frame, stat, p["prior_rates"], p["halflife"], p["shrink"])
        frame[f"{stat}_stat_v1"] = frame[f"{stat}_rate"] * frame["v1_minutes"]
        buckets = {int(k): float(v) for k, v in p["alpha_buckets"].items()}
        frame[f"{stat}_alpha"] = sm.alpha_for(frame[f"{stat}_stat_v1"].fillna(sm.MIN_MEAN), buckets)
        for name in sm.BASELINES:
            frame[f"{stat}_{name}"] = sm.baseline_mean(frame, stat, name)
    return frame


def _freeze_snapshot(db: Session, snapshot: NbaOutlookSnapshot, logs: pd.DataFrame, games: pd.DataFrame, parameters: dict, parameters_sha: str, now: datetime, *, dry_run: bool) -> dict:
    tipoff = ensure_utc(snapshot.scheduled_tipoff)
    cutoff = ensure_utc(snapshot.information_cutoff)
    if now >= tipoff:
        raise InformationLeakError(f"snapshot {snapshot.id} tipped off at {tipoff.isoformat()}")
    game = db.get(NbaGame, snapshot.game_id)
    outlooks = db.scalars(select(NbaRotationPrediction).where(NbaRotationPrediction.snapshot_id == snapshot.id)).all()
    with_minutes = [o for o in outlooks if o.expected_minutes_if_plays is not None]
    info = {"snapshot_id": snapshot.id, "game_id": snapshot.game_id, "label": snapshot.snapshot_label, "players": len(with_minutes), "no_minutes": len(outlooks) - len(with_minutes), "rows": 0}
    if not with_minutes:
        return info

    t_cut = pd.Timestamp(cutoff - GAME_RESULT_AVAILABILITY_LAG).tz_localize(None)
    rows = pd.DataFrame(
        {
            "player_id": [o.player_id for o in with_minutes], "game_id": snapshot.game_id, "team_id": [o.team_id for o in with_minutes],
            "season_start_year": game.season_start_year, "v1_minutes": [o.expected_minutes_if_plays for o in with_minutes],
            "t_cut": t_cut, "outlook": range(len(with_minutes)),
        }
    )
    known = logs[logs["recorded_at"] <= pd.Timestamp(cutoff).tz_localize(None)].drop(columns="recorded_at")
    frame = predict_rows(rows, known, games, parameters)

    for _, row in frame.iterrows():
        outlook = with_minutes[int(row["outlook"])]
        for stat in STATS:
            mean, alpha = row[f"{stat}_stat_v1"], row[f"{stat}_alpha"]
            if pd.isna(mean):  # no stat history known at this cutoff: not estimated
                continue
            info["rows"] += 1
            if dry_run:
                continue
            output = ProjectionOutput(
                market=MARKETS[stat], distribution=NbBucketDistribution(float(max(mean, sm.MIN_MEAN)), float(alpha)), distribution_kind=DISTRIBUTION_KINDS[stat],
                distribution_params={"alpha": float(alpha)}, games_of_history=outlook.history_games, expected_minutes=outlook.expected_minutes_if_plays, rate_per_minute=float(row[f"{stat}_rate"]),
                inputs={
                    "snapshot_id": snapshot.id, "snapshot_label": snapshot.snapshot_label, "snapshot_status": snapshot.status, "scheduled_tipoff": tipoff.isoformat(),
                    "rotation_prediction_id": outlook.id, "p_play": outlook.p_play, "minutes_model_run_id": outlook.minutes_model_run_id,
                    "parameters_sha256": parameters_sha, "baselines": {name: float(row[f"{stat}_{name}"]) for name in sm.BASELINES}, "validation": VALIDATION[stat],
                },
            )
            record_projection(db, game=game, player_id=outlook.player_id, team_id=outlook.team_id, model_name=parameters["model_name"], model_version=parameters["model_version"], information_cutoff=cutoff, output=output, now=now)
    return info


def freeze_due(db: Session, *, now: datetime | None = None, dry_run: bool = False) -> FreezeResult:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    result = FreezeResult(now=now)
    parameters, parameters_sha = load_parameters()
    candidates = db.scalars(
        select(NbaOutlookSnapshot).where(
            NbaOutlookSnapshot.snapshot_label.in_(FREEZE_LABELS), NbaOutlookSnapshot.status.in_(SNAPSHOT_STATUSES_WITH_ROWS), NbaOutlookSnapshot.information_cutoff >= now - timedelta(days=2)
        )
    ).all()
    tolerance = {s.label: s.tolerance for s in parse_specs(",".join(FREEZE_LABELS))}
    frozen_keys = {
        (g, ensure_utc(c))
        for g, c in db.execute(select(NbaPropProjection.game_id, NbaPropProjection.information_cutoff).where(NbaPropProjection.model_version == parameters["model_version"], NbaPropProjection.information_cutoff >= now - timedelta(days=2)).distinct()).all()
    }
    due = []
    for snap in candidates:
        if (snap.game_id, ensure_utc(snap.information_cutoff)) in frozen_keys:
            result.already_frozen += 1
        elif now > ensure_utc(snap.target_cutoff) + tolerance[snap.snapshot_label] or now >= ensure_utc(snap.scheduled_tipoff):
            result.window_closed.append({"snapshot_id": snap.id, "game_id": snap.game_id, "label": snap.snapshot_label})
        else:
            due.append(snap)
    if not due:
        return result

    games = load_games(db)
    player_ids = sorted(set(db.scalars(select(NbaRotationPrediction.player_id).where(NbaRotationPrediction.snapshot_id.in_([s.id for s in due]))).all()))
    logs = _load_known_logs(db, player_ids, now) if player_ids else pd.DataFrame()
    logs = logs[logs["game_id"].isin(games.loc[games["season_start_year"] >= DEFAULT_FIRST_SEASON, "game_id"])] if len(logs) else logs
    for snap in due:
        ids = {"snapshot_id": snap.id, "game_id": snap.game_id, "label": snap.snapshot_label}
        try:
            info = _freeze_snapshot(db, snap, logs, games, parameters, parameters_sha, now, dry_run=dry_run)
            if not dry_run:
                db.commit()
            result.frozen.append(info)
        except Exception as exc:  # one bad snapshot must not stop the others
            db.rollback()
            result.failed.append({**ids, "error": f"{type(exc).__name__}: {exc}"})
    return result


# --- evaluation: read-only, frozen rows only ---------------------------------


def _coverage(db: Session, version: str) -> dict:
    """Per label, from this version's first frozen cutoff: the snapshots with
    player rows, and which of them have no predictions (permanent gaps)."""
    frozen = {(g, ensure_utc(c)) for g, c in db.execute(select(NbaPropProjection.game_id, NbaPropProjection.information_cutoff).where(NbaPropProjection.model_version == version).distinct()).all()}
    first = min(c for _, c in frozen)
    snapshots = db.scalars(select(NbaOutlookSnapshot).where(NbaOutlookSnapshot.snapshot_label.in_(FREEZE_LABELS), NbaOutlookSnapshot.status.in_(SNAPSHOT_STATUSES_WITH_ROWS), NbaOutlookSnapshot.information_cutoff >= first)).all()
    coverage = {label: {"snapshots_with_rows": 0, "frozen": 0, "missing_snapshot_ids": []} for label in FREEZE_LABELS}
    for snap in snapshots:
        block = coverage[snap.snapshot_label]
        block["snapshots_with_rows"] += 1
        if (snap.game_id, ensure_utc(snap.information_cutoff)) in frozen:
            block["frozen"] += 1
        else:
            block["missing_snapshot_ids"].append(snap.id)
    return coverage


def evaluate_frozen(db: Session, *, model_version: str | None = None) -> dict:
    """Score the exact frozen predictions and baselines against final box
    scores. Nothing is recomputed. A DNP or a missing box score is void."""
    from app.nba.minutes.metrics import point_metrics
    from app.nba.minutes_v2.upper_bound import paired_bootstrap
    from app.nba.stats.experiment import distribution_block

    parameters, _ = load_parameters()
    version = model_version or parameters["model_version"]
    stmt = (
        select(NbaPropProjection, NbaGame.status, NbaGame.scheduled_start, NbaPlayerGameLog.did_not_play, NbaPlayerGameLog.minutes, NbaPlayerGameLog.points, NbaPlayerGameLog.rebounds, NbaPlayerGameLog.assists)
        .join(NbaGame, NbaGame.id == NbaPropProjection.game_id)
        .outerjoin(NbaPlayerGameLog, (NbaPlayerGameLog.game_id == NbaPropProjection.game_id) & (NbaPlayerGameLog.player_id == NbaPropProjection.player_id))
        .where(NbaPropProjection.model_version == version)
    )
    records = []
    for p, status, start, dnp, minutes, *stats in db.execute(stmt).all():
        stat = next(s for s, m in MARKETS.items() if m.value == p.market)
        actual = dict(zip(STATS, stats))[stat]
        played = dnp is False and minutes is not None and actual is not None
        tipoff = ensure_utc(datetime.fromisoformat(p.inputs["scheduled_tipoff"]))
        records.append(
            {
                "stat": stat, "label": p.inputs["snapshot_label"], "status": status, "played": played, "actual": float(actual) if played else np.nan, "stat_v1": p.predicted_mean,
                "alpha": p.distribution_params["alpha"], "kind": p.distribution_kind, "date": ensure_utc(start).strftime("%Y-%m-%d"), "game_id": p.game_id,
                "after_tipoff": ensure_utc(p.generated_at) >= tipoff, "before_cutoff": ensure_utc(p.generated_at) < ensure_utc(p.information_cutoff), **{f"b_{k}": v for k, v in p.inputs["baselines"].items()},
            }
        )
    report: dict = {"model_version": version, "rows": len(records), "integrity": {"generated_at_or_after_tipoff": 0, "generated_before_cutoff": 0}, "coverage": {}, "groups": {}}
    if not records:
        return report
    report["coverage"] = _coverage(db, version)
    frame = pd.DataFrame(records)
    report["integrity"] = {"generated_at_or_after_tipoff": int(frame["after_tipoff"].sum()), "generated_before_cutoff": int(frame["before_cutoff"].sum())}
    for (stat, label), group in frame.groupby(["stat", "label"]):
        final = group[group["status"] == "final"]
        scored = final[final["played"]]
        block: dict = {
            "validation": VALIDATION[stat], "distribution_kind": group["kind"].iloc[0], "frozen_rows": int(len(group)), "pending_rows": int(len(group) - len(final)),
            "void_rows": int(len(final) - len(scored)), "scored_rows": int(len(scored)), "games": int(scored["game_id"].nunique()),
        }
        if len(scored):
            block["means"] = {"stat_v1": point_metrics(scored["stat_v1"], scored["actual"]), **{name: point_metrics(scored[f"b_{name}"], scored["actual"]) for name in sm.BASELINES}}
            block["vs_baselines"] = {name: paired_bootstrap(scored[f"b_{name}"] - scored["actual"], scored["stat_v1"] - scored["actual"], scored["date"]) for name in sm.BASELINES}
            block["distribution"] = distribution_block(scored["actual"].to_numpy(), scored["stat_v1"].to_numpy(), scored["alpha"].to_numpy())
        report["groups"][f"{stat} {label}"] = block
    return report
