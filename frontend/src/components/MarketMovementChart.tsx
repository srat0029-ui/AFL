import type { BookmakerQuotePoint, ConsensusPoint, LineupStatusChange, ModelObservationPoint } from "../api/client";
import { niceHourTicks } from "../features/marketMovement";

const WIDTH = 720;
const HEIGHT = 320;
const PAD_LEFT = 48;
const PAD_RIGHT = 16;
const PAD_TOP = 16;
const PAD_BOTTOM = 40;
const PLOT_W = WIDTH - PAD_LEFT - PAD_RIGHT;
const PLOT_H = HEIGHT - PAD_TOP - PAD_BOTTOM;

/**
 * Real-observations-only timeline: bookmaker quotes (small dots, one colour
 * per identity role — not per bookmaker, to stay legible with many books),
 * bookmaker consensus (a connected line only between real consecutive
 * points — never smoothed/interpolated), model probability (a second,
 * visually distinct connected line), and lineup-status-change markers.
 * A series with fewer than two points draws a single dot, never a line —
 * there is nothing genuine to connect it to.
 */
function MarketMovementChart({
  bookmakerQuotes,
  consensusSeries,
  modelObservations,
  lineupStatusChanges,
}: {
  bookmakerQuotes: BookmakerQuotePoint[];
  consensusSeries: ConsensusPoint[];
  modelObservations: ModelObservationPoint[];
  lineupStatusChanges: LineupStatusChange[];
}) {
  const allHours = [
    ...bookmakerQuotes.map((q) => q.hours_to_kickoff),
    ...consensusSeries.map((c) => c.hours_to_kickoff),
    ...modelObservations.map((o) => o.hours_to_kickoff),
  ];
  const allProbs = [
    ...bookmakerQuotes.map((q) => q.raw_implied_probability),
    ...consensusSeries.map((c) => c.consensus_probability),
    ...modelObservations.map((o) => o.value),
  ];

  if (allHours.length === 0) {
    return (
      <div className="mme-chart__empty">No pre-kickoff observations to plot yet.</div>
    );
  }

  const maxHours = Math.max(...allHours, 1);
  const rawMin = Math.min(...allProbs);
  const rawMax = Math.max(...allProbs);
  const pad = Math.max(0.02, (rawMax - rawMin) * 0.15);
  const yMin = Math.max(0, rawMin - pad);
  const yMax = Math.min(1, rawMax + pad === rawMin ? rawMax + 0.05 : rawMax + pad);

  const toX = (hoursToKickoff: number) => PAD_LEFT + (1 - Math.min(hoursToKickoff, maxHours) / maxHours) * PLOT_W;
  const toY = (p: number) => PAD_TOP + PLOT_H - ((p - yMin) / (yMax - yMin || 1)) * PLOT_H;

  const hourTicks = niceHourTicks(maxHours);
  const probTicks = [yMin, yMin + (yMax - yMin) / 2, yMax];

  const consensusSorted = [...consensusSeries].sort((a, b) => a.hours_to_kickoff - b.hours_to_kickoff);
  const modelSorted = [...modelObservations].sort((a, b) => a.hours_to_kickoff - b.hours_to_kickoff);
  const consensusPath = consensusSorted.map((c) => `${toX(c.hours_to_kickoff)},${toY(c.consensus_probability)}`).join(" ");
  const modelPath = modelSorted.map((o) => `${toX(o.hours_to_kickoff)},${toY(o.value)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="mme-chart" role="img" aria-label="Market movement timeline">
      <line x1={PAD_LEFT} y1={PAD_TOP + PLOT_H} x2={WIDTH - PAD_RIGHT} y2={PAD_TOP + PLOT_H} className="mme-chart__axis" />
      <line x1={PAD_LEFT} y1={PAD_TOP} x2={PAD_LEFT} y2={PAD_TOP + PLOT_H} className="mme-chart__axis" />

      {probTicks.map((t, i) => (
        <g key={`y-${i}`}>
          <line x1={PAD_LEFT - 4} y1={toY(t)} x2={WIDTH - PAD_RIGHT} y2={toY(t)} className="mme-chart__gridline" />
          <text x={PAD_LEFT - 8} y={toY(t) + 4} className="mme-chart__tick-label" textAnchor="end">
            {(t * 100).toFixed(0)}%
          </text>
        </g>
      ))}

      {hourTicks.map((h) => (
        <g key={`x-${h}`}>
          <line x1={toX(h)} y1={PAD_TOP + PLOT_H} x2={toX(h)} y2={PAD_TOP + PLOT_H + 5} className="mme-chart__axis" />
          <text x={toX(h)} y={PAD_TOP + PLOT_H + 18} className="mme-chart__tick-label" textAnchor="middle">
            {h === 0 ? "Kickoff" : `${h}h`}
          </text>
        </g>
      ))}

      {lineupStatusChanges.map((c, i) => (
        <g key={`lineup-${i}`}>
          <line x1={toX(c.hours_to_kickoff)} y1={PAD_TOP} x2={toX(c.hours_to_kickoff)} y2={PAD_TOP + PLOT_H} className="mme-chart__lineup-line" />
          <path d={`M ${toX(c.hours_to_kickoff) - 4} ${PAD_TOP} l 4 7 l 4 -7 z`} className="mme-chart__lineup-marker">
            <title>{`Lineup status: ${c.from_status ?? "unknown"} → ${c.to_status ?? "unknown"} (${c.hours_to_kickoff.toFixed(1)}h before kickoff)`}</title>
          </path>
        </g>
      ))}

      {bookmakerQuotes.map((q, i) => (
        <circle key={`bq-${i}`} cx={toX(q.hours_to_kickoff)} cy={toY(q.raw_implied_probability)} r={2.5} className="mme-chart__bookmaker-point">
          <title>{`${q.bookmaker_name}: $${q.price_decimal.toFixed(2)} (${(q.raw_implied_probability * 100).toFixed(1)}%) — ${q.hours_to_kickoff.toFixed(1)}h before kickoff`}</title>
        </circle>
      ))}

      {consensusSorted.length >= 2 && <polyline points={consensusPath} className="mme-chart__consensus-line" />}
      {consensusSorted.map((c, i) => (
        <circle key={`c-${i}`} cx={toX(c.hours_to_kickoff)} cy={toY(c.consensus_probability)} r={3.5} className="mme-chart__consensus-point">
          <title>{`Consensus: ${(c.consensus_probability * 100).toFixed(1)}% (${c.n_bookmakers} bookmaker(s), ${c.n_devigged} de-vigged) — ${c.hours_to_kickoff.toFixed(1)}h before kickoff`}</title>
        </circle>
      ))}

      {modelSorted.length >= 2 && <polyline points={modelPath} className="mme-chart__model-line" />}
      {modelSorted.map((o, i) => (
        <circle key={`m-${i}`} cx={toX(o.hours_to_kickoff)} cy={toY(o.value)} r={3.5} className="mme-chart__model-point">
          <title>{`Model: ${(o.value * 100).toFixed(1)}% (${o.model_version}) — ${o.hours_to_kickoff.toFixed(1)}h before kickoff`}</title>
        </circle>
      ))}
    </svg>
  );
}

export default MarketMovementChart;
