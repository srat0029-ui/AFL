"""Elite disposal player monitoring diagnostic (Market Integrity + Final
Weekly Picks stage, Section 19) — a READ-ONLY research diagnostic over the
already-persisted PROMOTED disposal model's historical evaluation-period
predictions (player_disposal_predictions, from the original 2016-2025
backtest — see disposal_backtest.py). It never re-trains, re-tunes, or
touches the promoted model; it only asks whether that model's existing,
already-evaluated predictions show a systematic pattern for players with
a genuinely high historical disposal average.

The bucket a player falls into is computed from their own AVERAGE ACTUAL
disposal count across the evaluation period — ground truth, not the
model's own output and not reputation/name — so this can't circularly
"detect" the very bias it's trying to measure. This module reports
whatever the historical backtest data already shows; it does not draw on
or get influenced by any CURRENT-round observation (e.g.
model_market_disagreements.py's live Nick Daicos finding) — Section 19 is
explicit that the promoted model must never change based on current-round
observations, and this diagnostic exists precisely so any future decision
to revisit the model rests on the full historical record, not one round.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerDisposalPrediction, PlayerModelRun
from app.player_modelling.current_players import current_player_ids

_ELITE_MIN_AVG = 28.0
_HIGH_MIN_AVG = 22.0
_MID_MIN_AVG = 15.0

BUCKET_ELITE = "elite_28_plus"
BUCKET_HIGH = "high_22_to_28"
BUCKET_MID = "mid_15_to_22"
BUCKET_LOW = "low_under_15"

BUCKET_LABELS = {
    BUCKET_ELITE: "Elite (28+ avg disposals)",
    BUCKET_HIGH: "High (22-28 avg disposals)",
    BUCKET_MID: "Mid (15-22 avg disposals)",
    BUCKET_LOW: "Low (<15 avg disposals)",
}
BUCKET_ORDER = [BUCKET_ELITE, BUCKET_HIGH, BUCKET_MID, BUCKET_LOW]


def _bucket_for(avg_actual: float) -> str:
    if avg_actual >= _ELITE_MIN_AVG:
        return BUCKET_ELITE
    if avg_actual >= _HIGH_MIN_AVG:
        return BUCKET_HIGH
    if avg_actual >= _MID_MIN_AVG:
        return BUCKET_MID
    return BUCKET_LOW


@dataclass(frozen=True)
class PlayerBiasEntry:
    player_id: int
    player_name: str
    n_predictions: int
    avg_actual: float
    avg_predicted: float
    bias: float


@dataclass(frozen=True)
class BucketDiagnostic:
    bucket: str
    label: str
    n_players: int
    n_predictions: int
    avg_actual: float
    avg_predicted: float
    bias: float  # mean(predicted - actual); negative = model under-predicts (conservative)
    mae: float
    most_under_predicted_players: list[PlayerBiasEntry]  # most negative bias, i.e. most conservative


_player_bucket_cache: dict[int, str] | None = None
_player_bucket_cache_attempted = False


def player_bucket_lookup(db: Session) -> dict[int, str]:
    """player_id -> bucket name, for the Weekly Review stage's per
    -opportunity ELITE_PLAYER_CONSERVATIVE_MODEL uncertainty flag — cached
    at process level (same rationale as model_strength_context.py's Elo/
    Poisson cache) since this scans every persisted disposal prediction
    and the buckets only change after a real CLI retrain."""
    global _player_bucket_cache, _player_bucket_cache_attempted
    if _player_bucket_cache_attempted:
        return _player_bucket_cache or {}
    _player_bucket_cache_attempted = True

    model_run = db.scalar(
        select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True))
    )
    if model_run is None:
        _player_bucket_cache = {}
        return {}
    predictions = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == model_run.id)).all()
    by_player: dict[int, list[PlayerDisposalPrediction]] = defaultdict(list)
    for p in predictions:
        by_player[p.player_id].append(p)
    player_avg_actual = {pid: sum(p.actual_disposals for p in preds) / len(preds) for pid, preds in by_player.items()}
    _player_bucket_cache = {pid: _bucket_for(avg) for pid, avg in player_avg_actual.items()}
    return _player_bucket_cache


def load_elite_disposal_diagnostic(
    db: Session, *, top_n_players: int = 5, current_only: bool = True, min_n_predictions: int | None = 20
) -> list[BucketDiagnostic] | None:
    """`current_only` (default True — product-quality data-scoping fix)
    restricts which players appear in each bucket's displayed
    `most_under_predicted_players` list to currently active/relevant
    players (see current_players.py), so a long-retired player like Jack
    Watts doesn't show up on this current-facing diagnostic. It does NOT
    change n_players/n_predictions/avg_actual/avg_predicted/bias/mae —
    those bucket-level aggregates always reflect the FULL historical
    evaluation population, exactly as before, since this is a research
    diagnostic over the promoted model's historical backtest and must never
    silently change based on which players happen to still be playing.

    `min_n_predictions` (default 20) is a second, independent DISPLAY-ONLY
    filter on the same player list: a player needs at least this many
    historical predictions of their own to appear, so a one-game sample
    doesn't sit next to a 150-prediction veteran. None means no minimum
    ("All"). Like current_only, this never touches the bucket aggregates."""
    model_run = db.scalar(
        select(PlayerModelRun).where(PlayerModelRun.market == "player_disposals", PlayerModelRun.is_promoted.is_(True))
    )
    if model_run is None:
        return None

    predictions = db.scalars(select(PlayerDisposalPrediction).where(PlayerDisposalPrediction.model_run_id == model_run.id)).all()
    if not predictions:
        return None

    by_player: dict[int, list[PlayerDisposalPrediction]] = defaultdict(list)
    for p in predictions:
        by_player[p.player_id].append(p)

    # Ground-truth bucket, one per player, computed from their own actual
    # disposal history in the eval set (not model output, not reputation).
    player_avg_actual = {pid: sum(p.actual_disposals for p in preds) / len(preds) for pid, preds in by_player.items()}
    player_bucket = {pid: _bucket_for(avg) for pid, avg in player_avg_actual.items()}

    player_names = {
        p.id: p.display_name for p in db.scalars(select(Player).where(Player.id.in_(by_player.keys()))).all()
    }

    grouped: dict[str, list[int]] = defaultdict(list)
    for pid in by_player:
        grouped[player_bucket[pid]].append(pid)

    # Computed once, outside the loop — restricts only the DISPLAYED player
    # list below, never the bucket-level aggregates above/below it.
    current_ids = current_player_ids(db) if current_only else None

    results = []
    for bucket in BUCKET_ORDER:
        player_ids = grouped.get(bucket, [])
        if not player_ids:
            continue

        bucket_preds = [p for pid in player_ids for p in by_player[pid]]
        n_predictions = len(bucket_preds)
        avg_actual = sum(p.actual_disposals for p in bucket_preds) / n_predictions
        avg_predicted = sum(p.predicted_mean for p in bucket_preds) / n_predictions
        bias = sum(p.predicted_mean - p.actual_disposals for p in bucket_preds) / n_predictions
        mae = sum(abs(p.predicted_mean - p.actual_disposals) for p in bucket_preds) / n_predictions

        # The player LIST is current-scoped (when current_only) and sample-
        # size-scoped (when min_n_predictions is set); the aggregates above
        # (n_predictions/avg_actual/avg_predicted/bias/mae) were already
        # computed from the full, unfiltered player_ids and are untouched.
        display_ids = player_ids if current_ids is None else [pid for pid in player_ids if pid in current_ids]
        if min_n_predictions is not None:
            display_ids = [pid for pid in display_ids if len(by_player[pid]) >= min_n_predictions]

        player_entries = []
        for pid in display_ids:
            preds = by_player[pid]
            p_avg_actual = sum(p.actual_disposals for p in preds) / len(preds)
            p_avg_predicted = sum(p.predicted_mean for p in preds) / len(preds)
            p_bias = sum(p.predicted_mean - p.actual_disposals for p in preds) / len(preds)
            player_entries.append(
                PlayerBiasEntry(
                    player_id=pid,
                    player_name=player_names.get(pid, f"Player {pid}"),
                    n_predictions=len(preds),
                    avg_actual=p_avg_actual,
                    avg_predicted=p_avg_predicted,
                    bias=p_bias,
                )
            )
        player_entries.sort(key=lambda e: e.bias)  # most negative (most under-predicted) first

        results.append(
            BucketDiagnostic(
                bucket=bucket,
                label=BUCKET_LABELS[bucket],
                n_players=len(player_ids),
                n_predictions=n_predictions,
                avg_actual=avg_actual,
                avg_predicted=avg_predicted,
                bias=bias,
                mae=mae,
                most_under_predicted_players=player_entries[:top_n_players],
            )
        )
    return results


def bucket_diagnostic_as_dict(b: BucketDiagnostic) -> dict:
    return {
        "bucket": b.bucket,
        "label": b.label,
        "n_players": b.n_players,
        "n_predictions": b.n_predictions,
        "avg_actual": b.avg_actual,
        "avg_predicted": b.avg_predicted,
        "bias": b.bias,
        "mae": b.mae,
        "most_under_predicted_players": [
            {
                "player_id": e.player_id,
                "player_name": e.player_name,
                "n_predictions": e.n_predictions,
                "avg_actual": e.avg_actual,
                "avg_predicted": e.avg_predicted,
                "bias": e.bias,
            }
            for e in b.most_under_predicted_players
        ],
    }
