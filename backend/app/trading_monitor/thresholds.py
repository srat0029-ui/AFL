"""Every threshold the Trading Monitor layer uses, in one place, each
documented with WHY it exists and whether it's empirically grounded or a
conservative placeholder — per this project's own discipline of never
scattering magic numbers through detection code (see app/market_monitor/
detector.py's identical convention, which this module follows rather than
inventing a second style).

Two kinds of threshold live here:

1. References to ALREADY-CALIBRATED thresholds elsewhere in this codebase
   (market_monitor's divergence/dispersion cutoffs, calibrated once against
   a 2,653-alert dataset; the SGM correlation-adjustment buckets shipped
   last phase). These are IMPORTED, never redefined — one source of truth.

2. Genuinely new thresholds for model-side movement (team win probability,
   player projections). No historical distribution of cycle-to-cycle model
   movement exists anywhere in this codebase — `ModelValueObservation`
   (app/models/model_value_observation.py) is brand new this phase, so
   there is nothing to empirically derive these from yet. They are
   deliberately chosen in the same neighbourhood as market_monitor's own
   calibrated probability-gap thresholds (8pp/15pp divergence, 10pp/20pp
   dispersion) since both describe "is this probability gap large enough
   to matter," but are explicitly CONSERVATIVE DEFAULTS, not statistically
   validated — revisit once real observation history accumulates (see
   README roadmap).
"""

from app.market_monitor.detector import (
    DISPERSION_CRITICAL_PP,
    DISPERSION_WARN_PP,
    DIVERGENCE_CRITICAL_PP,
    DIVERGENCE_WARN_PP,
)
from app.player_modelling.sgm_prospective_evaluation import CORRELATION_ADJUSTMENT_BUCKETS

__all__ = [
    "DIVERGENCE_WARN_PP", "DIVERGENCE_CRITICAL_PP", "DISPERSION_WARN_PP", "DISPERSION_CRITICAL_PP",
    "CORRELATION_ADJUSTMENT_BUCKETS", "MIN_BOOKMAKERS_FOR_DISPERSION",
    "TEAM_PROBABILITY_NOTABLE_PP", "TEAM_PROBABILITY_MATERIAL_PP",
    "PLAYER_PROBABILITY_NOTABLE_PP", "PLAYER_PROBABILITY_MATERIAL_PP",
    "DISPOSAL_PROJECTED_MEAN_NOTABLE", "DISPOSAL_PROJECTED_MEAN_MATERIAL",
    "GOAL_PROJECTED_MEAN_NOTABLE", "GOAL_PROJECTED_MEAN_MATERIAL",
    "SGM_MOVEMENT_NOISE_MULTIPLE", "STALE_LIVE_CYCLE_RUNS_TO_CHECK",
]

# Same floor app.player_modelling.consensus_and_outliers.detect_outlier_bookmaker
# already requires before an outlier check is meaningful - referenced here,
# not redefined, so a dispersion table row never appears with fewer books
# than the detector itself would trust.
MIN_BOOKMAKERS_FOR_DISPERSION = 3

# --- NEW, conservative-default thresholds (see module docstring, point 2) ---

# Team win-probability movement between two consecutive cycles.
TEAM_PROBABILITY_NOTABLE_PP = 0.03
TEAM_PROBABILITY_MATERIAL_PP = 0.07

# Player disposal/goal probability movement (at a fixed preset threshold)
# between two consecutive cycles. Slightly looser than team markets since
# player projections naturally carry more model-driven variance cycle to
# cycle (form updates, usage-regime shifts) even without genuine news.
PLAYER_PROBABILITY_NOTABLE_PP = 0.05
PLAYER_PROBABILITY_MATERIAL_PP = 0.10

# Player projected-mean movement, in the projection's own units (not
# probability points). Calibrated so the project's own motivating example
# (Nick Daicos "29.4 -> 31.1 disposals" alongside a lineup-status change) is
# at least "notable" - a threshold that missed its own worked example would
# be miscalibrated by definition, even as a conservative placeholder.
DISPOSAL_PROJECTED_MEAN_NOTABLE = 1.5
DISPOSAL_PROJECTED_MEAN_MATERIAL = 3.5
GOAL_PROJECTED_MEAN_NOTABLE = 0.25
GOAL_PROJECTED_MEAN_MATERIAL = 0.5

# SGM joint-probability movement: grounded in the engine's OWN computed
# Monte Carlo uncertainty (SgmPriceSnapshot.mc_standard_error), not an
# arbitrary constant - a genuinely statistically-motivated threshold, unlike
# the conservative defaults above. A move smaller than this many combined
# standard errors isn't distinguishable from simulation noise at roughly a
# 99.7% level (3 SDs) - directly answers this phase's "don't treat normal
# Monte Carlo noise as meaningful movement" requirement with real numbers
# already computed by the pricing engine, not a guess.
SGM_MOVEMENT_NOISE_MULTIPLE = 3.0

# How many of the most recent LiveCycleRun rows to inspect for "did the
# live cycle fail recently" - a small, fixed operational window, not a
# statistical threshold.
STALE_LIVE_CYCLE_RUNS_TO_CHECK = 5
