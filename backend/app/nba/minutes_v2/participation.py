"""Minutes V2 participation: recalibrating a history-only probability by
injury-feed evidence (docs/NBA_MINUTES_V2_PLAN.md, step 2).

    logit p' = logit p + a[state] + b[state] * logit p

with an L2 penalty on (a, b), so every state is shrunk toward "no change"
(p' = p) and a state with little data barely moves. p is either P(dress |
history) (V2's factorised form) or V1.5's frozen P(play) (the fallback).
States come from the evidence stored with each outlook
(app.nba.rotation.prospective.availability_payload). The feed publishes only
Out and Day-To-Day in season (docs/NBA_MINUTES_V2_EVIDENCE_AUDIT.md).

Fitted on design-period snapshots only; until then nothing here is used.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

EPS = 1e-4
STATES = ("never_listed", "no_longer_listed", "out", "day_to_day", "listed_other", "feed_not_read")


def evidence_state(evidence: dict | None) -> str:
    """Canonical state of an outlook's `availability_evidence`."""
    state = (evidence or {}).get("state")
    if state == "listed":
        if evidence.get("status") == "out":
            return "out"
        if (evidence.get("source_status") or "").lower() == "day-to-day":
            return "day_to_day"
        return "listed_other"
    if state in ("never_listed", "no_longer_listed"):
        return state
    return "feed_not_read"


def out_rule(p, states) -> np.ndarray:
    """The comparator V2 must beat: listed Out -> 0, otherwise unchanged."""
    p = np.asarray(p, dtype=float)
    return np.where(np.asarray(states) == "out", 0.0, p)


@dataclass
class EvidenceRecalibration:
    penalty: float = 1.0  # L2 strength on (a, b); larger = closer to "no change"
    coef_: dict = field(default_factory=dict)  # state -> (a, b)

    def _design(self, p, states):
        z = logit(np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS))
        states = np.asarray(states)
        unknown = set(np.unique(states)) - set(STATES)
        if unknown:
            raise ValueError(f"unknown evidence states {sorted(unknown)}")
        onehot = np.stack([(states == s).astype(float) for s in STATES], axis=1)
        return z, np.hstack([onehot, onehot * z[:, None]])

    def fit(self, p, states, y) -> "EvidenceRecalibration":
        z, x = self._design(p, states)
        y = np.asarray(y, dtype=float)

        def objective(beta):
            eta = z + x @ beta
            loss = np.sum(np.logaddexp(0.0, eta) - y * eta)
            grad = x.T @ (expit(eta) - y)
            return loss + 0.5 * self.penalty * beta @ beta, grad + self.penalty * beta

        beta = minimize(objective, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B").x
        k = len(STATES)
        self.coef_ = {s: (float(beta[i]), float(beta[k + i])) for i, s in enumerate(STATES)}
        return self

    def predict(self, p, states) -> np.ndarray:
        if not self.coef_:
            raise RuntimeError("fit first")
        z, x = self._design(p, states)
        beta = np.array([self.coef_[s][0] for s in STATES] + [self.coef_[s][1] for s in STATES])
        return expit(z + x @ beta)
