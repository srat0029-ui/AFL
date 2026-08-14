import type { CalibrationBucket } from "../api/client";

const WIDTH = 320;
const HEIGHT = 320;
const PAD = 40;
const PLOT = WIDTH - PAD * 2;

// Buckets are on a 50%-100% scale (favourite-perspective calibration —
// see app/modelling/metrics.py::favourite_calibration_table), so the chart
// axes match that range rather than the full 0-100%.
const AXIS_MIN = 0.5;
const AXIS_MAX = 1.0;

function toX(value: number): number {
  return PAD + ((value - AXIS_MIN) / (AXIS_MAX - AXIS_MIN)) * PLOT;
}

function toY(value: number): number {
  return HEIGHT - PAD - ((value - AXIS_MIN) / (AXIS_MAX - AXIS_MIN)) * PLOT;
}

function radiusForN(n: number, maxN: number): number {
  if (maxN <= 0) return 3;
  return 3 + 9 * Math.sqrt(n / maxN);
}

/** Reliability diagram: predicted probability (x) vs. observed win frequency
 * (y) for each calibration bucket, against the perfect-calibration diagonal.
 * Point size reflects sample size so a tiny bucket doesn't visually compete
 * with a well-populated one. */
function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  const populated = buckets.filter((b) => b.n > 0 && b.avg_predicted !== null && b.actual_rate !== null);
  const maxN = Math.max(1, ...populated.map((b) => b.n));

  const ticks = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0];

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="calibration-chart" role="img" aria-label="Calibration reliability chart">
      {/* perfect-calibration diagonal */}
      <line x1={toX(AXIS_MIN)} y1={toY(AXIS_MIN)} x2={toX(AXIS_MAX)} y2={toY(AXIS_MAX)} className="calibration-chart__diagonal" />

      {/* axes */}
      <line x1={PAD} y1={HEIGHT - PAD} x2={WIDTH - PAD} y2={HEIGHT - PAD} className="calibration-chart__axis" />
      <line x1={PAD} y1={PAD} x2={PAD} y2={HEIGHT - PAD} className="calibration-chart__axis" />

      {ticks.map((t) => (
        <g key={`x-${t}`}>
          <line x1={toX(t)} y1={HEIGHT - PAD} x2={toX(t)} y2={HEIGHT - PAD + 5} className="calibration-chart__axis" />
          <text x={toX(t)} y={HEIGHT - PAD + 18} className="calibration-chart__tick-label" textAnchor="middle">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}
      {ticks.map((t) => (
        <g key={`y-${t}`}>
          <line x1={PAD - 5} y1={toY(t)} x2={PAD} y2={toY(t)} className="calibration-chart__axis" />
          <text x={PAD - 9} y={toY(t) + 4} className="calibration-chart__tick-label" textAnchor="end">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      <text x={WIDTH / 2} y={HEIGHT - 4} className="calibration-chart__axis-label" textAnchor="middle">
        Predicted probability (favourite)
      </text>
      <text x={12} y={HEIGHT / 2} className="calibration-chart__axis-label" textAnchor="middle" transform={`rotate(-90 12 ${HEIGHT / 2})`}>
        Actual win rate
      </text>

      {populated.map((b) => (
        <circle
          key={b.bucket}
          cx={toX(b.avg_predicted as number)}
          cy={toY(b.actual_rate as number)}
          r={radiusForN(b.n, maxN)}
          className="calibration-chart__point"
        >
          <title>
            {b.bucket}: predicted {((b.avg_predicted as number) * 100).toFixed(1)}%, actual {((b.actual_rate as number) * 100).toFixed(1)}% (n={b.n})
          </title>
        </circle>
      ))}
    </svg>
  );
}

export default CalibrationChart;
