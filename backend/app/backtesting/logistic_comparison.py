"""Elo vs. logistic-regression disagreement analysis — the same bucketing
idea as app/backtesting/model_comparison.py's Elo-vs-Poisson comparison,
applied to the logistic candidate. Answers: when the two disagree a lot,
which one has historically been more trustworthy? Useful later for
deciding between replacing Elo, ensembling, or using disagreement itself as
a confidence signal — not acted on automatically here.
"""

from dataclasses import dataclass

from app.backtesting.segments import win_prob_metrics_from_pairs

_DISAGREEMENT_BINS = [
    (0.0, 0.05, "agree within 5pp"),
    (0.05, 0.10, "disagree 5-10pp"),
    (0.10, 0.20, "disagree 10-20pp"),
    (0.20, 1.01, "disagree 20pp+"),
]


@dataclass(frozen=True)
class LogisticDisagreementBucket:
    label: str
    n: int
    elo_metrics: dict[str, float]
    logistic_metrics: dict[str, float]
    actual_home_win_rate: float | None


@dataclass(frozen=True)
class LogisticComparisonReport:
    n_matches: int
    mean_absolute_disagreement: float
    disagreement_buckets: list[LogisticDisagreementBucket]


def build_logistic_vs_elo_comparison(elo_probs_by_match: dict[int, float], logistic_preds: list) -> LogisticComparisonReport:
    """logistic_preds: list[LogisticPrediction] (see app/modelling/logistic.py)."""
    paired = [(elo_probs_by_match[p.match_id], p) for p in logistic_preds if p.match_id in elo_probs_by_match]
    if not paired:
        return LogisticComparisonReport(n_matches=0, mean_absolute_disagreement=float("nan"), disagreement_buckets=[])

    disagreements = [abs(elo_p - logp.home_win_probability) for elo_p, logp in paired]
    mean_disagreement = sum(disagreements) / len(disagreements)

    buckets = []
    for lo, hi, label in _DISAGREEMENT_BINS:
        group = [(elo_p, logp) for (elo_p, logp), d in zip(paired, disagreements) if lo <= d < hi]
        if not group:
            buckets.append(
                LogisticDisagreementBucket(label=label, n=0, elo_metrics={}, logistic_metrics={}, actual_home_win_rate=None)
            )
            continue
        elo_probs_group = [e for e, _ in group]
        logistic_probs_group = [pred.home_win_probability for _, pred in group]
        outcomes_group = [pred.actual_home_outcome for _, pred in group]
        buckets.append(
            LogisticDisagreementBucket(
                label=label,
                n=len(group),
                elo_metrics=win_prob_metrics_from_pairs(elo_probs_group, outcomes_group),
                logistic_metrics=win_prob_metrics_from_pairs(logistic_probs_group, outcomes_group),
                actual_home_win_rate=sum(outcomes_group) / len(outcomes_group),
            )
        )

    return LogisticComparisonReport(n_matches=len(paired), mean_absolute_disagreement=mean_disagreement, disagreement_buckets=buckets)
