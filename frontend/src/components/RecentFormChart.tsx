import "./RecentFormChart.css";

interface RecentFormChartProps {
  values: number[]; // chronological, most recent last
  threshold: number | null;
  predictedMean: number | null;
  lineType: string | null;
}

/** Section 13: a small visual making it obvious where the market line
 * sits relative to the player's recent actual results and the model's
 * own forecast — never presented as proof of anything, just context. */
function RecentFormChart({ values, threshold, predictedMean, lineType }: RecentFormChartProps) {
  if (values.length === 0) {
    return <p className="empty-state">No recent history available.</p>;
  }

  const width = 320;
  const height = 110;
  const padding = { top: 10, right: 10, bottom: 20, left: 10 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const maxValue = Math.max(...values, threshold ?? 0, predictedMean ?? 0) * 1.15;
  const barWidth = chartWidth / values.length;
  const yFor = (v: number) => padding.top + chartHeight - (v / maxValue) * chartHeight;

  const meetsThreshold = (v: number) => {
    if (threshold === null) return null;
    return lineType === "multi_plus" ? v >= threshold : v > threshold;
  };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="recent-form-chart" role="img" aria-label="Recent form chart">
      {threshold !== null && (
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={yFor(threshold)}
          y2={yFor(threshold)}
          className="recent-form-chart__threshold-line"
        />
      )}
      {predictedMean !== null && (
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={yFor(predictedMean)}
          y2={yFor(predictedMean)}
          className="recent-form-chart__mean-line"
        />
      )}
      {values.map((v, i) => {
        const hit = meetsThreshold(v);
        const barHeight = chartHeight - (yFor(v) - padding.top);
        return (
          <rect
            key={i}
            x={padding.left + i * barWidth + barWidth * 0.15}
            y={yFor(v)}
            width={barWidth * 0.7}
            height={Math.max(barHeight, 1)}
            className={hit === null ? "recent-form-chart__bar" : hit ? "recent-form-chart__bar recent-form-chart__bar--hit" : "recent-form-chart__bar recent-form-chart__bar--miss"}
          >
            <title>{v}</title>
          </rect>
        );
      })}
      <text x={padding.left} y={height - 4} className="recent-form-chart__label">
        {values.length} most recent games
      </text>
      {threshold !== null && (
        <text x={width - padding.right} y={yFor(threshold) - 3} textAnchor="end" className="recent-form-chart__line-label">
          line {threshold}
        </text>
      )}
      {predictedMean !== null && (
        <text x={width - padding.right} y={yFor(predictedMean) - 3} textAnchor="end" className="recent-form-chart__mean-label">
          model {predictedMean.toFixed(1)}
        </text>
      )}
    </svg>
  );
}

export default RecentFormChart;
