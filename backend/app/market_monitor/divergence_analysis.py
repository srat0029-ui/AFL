"""Divergence-clustering analysis (item 6): purely descriptive grouping of
current MODEL_VS_MARKET_DIVERGENCE cases by market family, probability
range, player historical volume, history size, usage regime, model
version, and time-to-kickoff — to surface HYPOTHESES for future historical
research, never to retune anything live (item 9's explicit boundary; this
module writes nothing and changes no threshold/weight/probability)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from app.market_monitor.case_builder import AnomalyCase
from app.market_monitor.common import aware
from app.market_monitor.types import MODEL_VS_MARKET_DIVERGENCE

VOLUME_EDGES = (0, 15, 22, 28, 1000)
VOLUME_LABELS = ("<15", "15-22", "22-28", "28+")
PROB_EDGES = (0.0, 0.10, 0.25, 0.50, 0.75, 1.0)
PROB_LABELS = ("0-10%", "10-25%", "25-50%", "50-75%", "75-100%")


def _bucket(value: float | None, edges, labels) -> str:
    if value is None:
        return "unknown"
    for lo, hi, label in zip(edges[:-1], edges[1:], labels):
        if lo <= value < hi:
            return label
    return labels[-1]


@dataclass(frozen=True)
class ClusterBucket:
    dimension: str
    key: str
    n: int
    mean_magnitude_pp: float


def analyze_divergence_clusters(
    cases: list[AnomalyCase], *, kickoff_by_match: dict[int, datetime] | None = None,
    career_avg_by_player: dict[int, float] | None = None, games_of_history_by_player: dict[int, int] | None = None,
    now: datetime | None = None,
) -> list[ClusterBucket]:
    """career_avg_by_player/games_of_history_by_player: optional, supplied
    by the caller (a single cheap bulk query over the already-computed
    round pricing — see scripts/market_monitor_prospective_verification.py)
    rather than this function re-querying per case."""
    now = now or datetime.now(timezone.utc)
    kickoff_by_match = kickoff_by_match or {}
    career_avg_by_player = career_avg_by_player or {}
    games_of_history_by_player = games_of_history_by_player or {}
    divergence_cases = [c for c in cases if c.primary_alert.alert_type == MODEL_VS_MARKET_DIVERGENCE or MODEL_VS_MARKET_DIVERGENCE in c.supporting_alert_types]

    dims: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for c in divergence_cases:
        div_alert = next((a for a in c.alerts if a.alert_type == MODEL_VS_MARKET_DIVERGENCE), c.primary_alert)
        magnitude = div_alert.magnitude if div_alert.magnitude is not None else 0.0

        dims["market_family"][c.market_type].append(magnitude)
        dims["probability_range"][_bucket(div_alert.model_probability, PROB_EDGES, PROB_LABELS)].append(magnitude)
        dims["usage_regime"][div_alert.model_risk_flags[0].code if div_alert.model_risk_flags else "none"].append(magnitude)
        dims["model_version"][div_alert.model_version or "unknown"].append(magnitude)
        if c.player_id in career_avg_by_player:
            dims["player_historical_volume"][_bucket(career_avg_by_player[c.player_id], VOLUME_EDGES, VOLUME_LABELS)].append(magnitude)
        if c.player_id in games_of_history_by_player:
            n_games = games_of_history_by_player[c.player_id]
            hist_bucket = "<10 games" if n_games < 10 else ("10-50 games" if n_games < 50 else "50+ games")
            dims["player_history_size"][hist_bucket].append(magnitude)

        kickoff = kickoff_by_match.get(c.match_id)
        if kickoff is not None:
            hours = max((aware(kickoff) - now).total_seconds() / 3600.0, 0.0)
            bucket = "<24h" if hours < 24 else ("24-72h" if hours < 72 else "72h+")
        else:
            bucket = "unknown"
        dims["time_to_kickoff"][bucket].append(magnitude)

    buckets = []
    for dimension, groups in dims.items():
        for key, magnitudes in groups.items():
            buckets.append(ClusterBucket(dimension=dimension, key=key, n=len(magnitudes), mean_magnitude_pp=sum(magnitudes) / len(magnitudes)))
    return sorted(buckets, key=lambda b: (b.dimension, -b.n))
