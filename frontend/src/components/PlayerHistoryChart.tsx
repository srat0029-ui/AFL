interface HistoryPoint {
  match_id: number;
  scheduled_start: string;
  stat_value: number | null;
  highlighted: boolean;
}

/** A compact chronological bar chart of every recorded game behind a
 * comparison — the "visual history" step between the headline split and
 * the written explanation, so a shift (or its absence) is visible before
 * it's described in words. Purely presentational: it re-plots evidence
 * rows the page already fetched, computes nothing new, and never implies
 * a trend line the underlying data doesn't support. Games with no
 * recorded value for this statistic are left out of the plot (never
 * drawn as zero) and counted in the caption below. */
function PlayerHistoryChart({
  points,
  stat,
  highlightedLabel,
  otherLabel,
}: {
  points: HistoryPoint[];
  stat: string;
  highlightedLabel: string;
  otherLabel: string;
}) {
  const plotted = [...points]
    .filter((p) => p.stat_value !== null)
    .sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start) || a.match_id - b.match_id);
  const missing = points.length - plotted.length;

  if (plotted.length === 0) {
    return <p className="hint research-history-chart__empty">No recorded values available to plot for this comparison.</p>;
  }

  const max = Math.max(...plotted.map((p) => p.stat_value!), 1);
  const barWidth = 10;
  const gap = 4;
  const height = 120;
  const width = plotted.length * (barWidth + gap) + gap;

  return (
    <div className="research-history-chart">
      <div className="research-history-chart__scroll" role="region" aria-label={`Chronological ${stat} history`} tabIndex={0}>
        <svg viewBox={`0 0 ${Math.max(width, 200)} ${height + 20}`} width={Math.max(width, 200)} height={height + 20} className="research-history-chart__svg">
          {plotted.map((p, i) => {
            const x = gap + i * (barWidth + gap);
            const barHeight = Math.max(2, (p.stat_value! / max) * height);
            const y = height - barHeight;
            return (
              <rect
                key={p.match_id}
                x={x}
                y={y}
                width={barWidth}
                height={barHeight}
                rx={2}
                className={p.highlighted ? "research-history-chart__bar research-history-chart__bar--with" : "research-history-chart__bar research-history-chart__bar--without"}
              >
                <title>
                  {new Date(p.scheduled_start).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" })} — {p.stat_value} {stat} ({p.highlighted ? highlightedLabel : otherLabel})
                </title>
              </rect>
            );
          })}
          <line x1={0} y1={height} x2={width} y2={height} className="research-history-chart__axis" />
        </svg>
      </div>
      <div className="research-history-chart__legend">
        <span className="research-history-chart__legend-item">
          <span className="research-history-chart__swatch research-history-chart__swatch--with" /> {highlightedLabel}
        </span>
        <span className="research-history-chart__legend-item">
          <span className="research-history-chart__swatch research-history-chart__swatch--without" /> {otherLabel}
        </span>
        <span className="hint">Oldest → newest, left to right. {missing > 0 && `${missing} game(s) with no recorded ${stat} excluded.`}</span>
      </div>
    </div>
  );
}

export default PlayerHistoryChart;
