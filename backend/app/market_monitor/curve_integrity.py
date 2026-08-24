"""Player pricing curve integrity (item 2): for a family of thresholds on
the SAME player/match/market (e.g. disposals 15.5/20.5/25.5/30.5/35.5),
P(over lower threshold) must be >= P(over higher threshold) — the curve is
a survival function, so it can only be flat or decreasing as the threshold
rises. Checked independently for the model's own probabilities and, where
a bookmaker has actually quoted that EXACT threshold (never interpolated
or compared across non-equivalent lines), for market-implied probability
too.

Two distinct failure modes, both real trading-desk QA concerns:
  - Non-monotonic: probability at a threshold is HIGHER than at a lower
    threshold — a structural violation, always worth flagging regardless
    of magnitude (see NON_MONOTONIC_MIN_PP below for the noise floor).
  - Adjacent-threshold jump: the curve is technically monotonic (never
    increases) but one gap between neighbouring thresholds is much larger
    than the other gaps on the same curve — a plausible sign one specific
    line is mispriced/stale relative to its neighbours, even though
    nothing is strictly "broken."
"""

from dataclasses import dataclass

# A monotonicity violation smaller than this is treated as floating-point/
# rounding noise from the distribution PMF, not a real inversion — real
# curve breaks observed in this engine's data are on the order of whole
# percentage points, not fractions of one.
NON_MONOTONIC_MIN_PP = 0.005

# A gap between adjacent thresholds counts as an "anomalous jump" only if
# it clears BOTH: (a) an absolute floor, so a curve where every gap is tiny
# never fires on noise, and (b) a multiple of the OTHER gaps' median on the
# same curve, so a genuinely steep-but-uniform curve (e.g. a very high-
# volume player where every 5-disposal step costs a lot of probability)
# isn't flagged just for being steep everywhere.
JUMP_ABSOLUTE_FLOOR_PP = 0.05
JUMP_RATIO_VS_OTHER_GAPS = 3.0


@dataclass(frozen=True)
class CurvePoint:
    threshold: float
    probability: float


@dataclass(frozen=True)
class MonotonicityResult:
    is_monotonic: bool
    violations: list[tuple[CurvePoint, CurvePoint]]  # (lower_threshold_point, higher_threshold_point) pairs that inverted


def check_monotonicity(points: list[CurvePoint]) -> MonotonicityResult:
    ordered = sorted(points, key=lambda p: p.threshold)
    violations = []
    for lo, hi in zip(ordered, ordered[1:]):
        if hi.probability - lo.probability > NON_MONOTONIC_MIN_PP:
            violations.append((lo, hi))
    return MonotonicityResult(is_monotonic=not violations, violations=violations)


@dataclass(frozen=True)
class JumpResult:
    lower: CurvePoint
    upper: CurvePoint
    gap: float
    other_gaps_median: float


def find_adjacent_jumps(points: list[CurvePoint]) -> list[JumpResult]:
    """Only meaningful with >= 3 points (need at least one neighbouring gap
    to compare against) — a 2-point curve returns no jumps, not a false one.

    Compares each gap to its IMMEDIATE neighbouring gap(s) only, not a
    global median across the whole curve. A monotonically decreasing
    threshold curve (e.g. a count distribution's survival function) decays
    geometrically by construction — its first gap is routinely far larger
    than its tail gaps purely from curve shape, which a global-median
    comparison would misflag as "the first threshold is anomalous" on
    almost every real curve. Comparing only to the gap(s) immediately
    either side matches item 2's own framing: "one threshold materially
    diverging while NEIGHBOURING thresholds look normal" — a genuine local
    spike, not the curve's ordinary decay shape."""
    ordered = sorted(points, key=lambda p: p.threshold)
    if len(ordered) < 3:
        return []

    gaps = [(lo, hi, lo.probability - hi.probability) for lo, hi in zip(ordered, ordered[1:])]
    results = []
    for i, (lo, hi, gap) in enumerate(gaps):
        neighbours = [g for j, (_, _, g) in enumerate(gaps) if j in (i - 1, i + 1) and 0 <= j < len(gaps)]
        if not neighbours:
            continue
        neighbours_avg = sum(neighbours) / len(neighbours)
        if gap >= JUMP_ABSOLUTE_FLOOR_PP and neighbours_avg > 0 and gap >= JUMP_RATIO_VS_OTHER_GAPS * neighbours_avg:
            results.append(JumpResult(lower=lo, upper=hi, gap=gap, other_gaps_median=neighbours_avg))
    return results
