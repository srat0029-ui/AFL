import type { PoissonRevisionComparison as PoissonRevisionComparisonData, PoissonVariantReport, RoundBandMetrics } from "../api/client";

function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function RoundBandTable({ original, revised }: { original: RoundBandMetrics[]; revised: RoundBandMetrics[] }) {
  const revisedByLabel = Object.fromEntries(revised.map((b) => [b.label, b]));
  return (
    <div className="segment-table-scroll">
      <table className="segment-table">
        <thead>
          <tr>
            <th>Band</th>
            <th>N</th>
            <th>Original total MAE</th>
            <th>Revised total MAE</th>
            <th>Original total bias</th>
            <th>Revised total bias</th>
          </tr>
        </thead>
        <tbody>
          {original.map((band) => {
            const r = revisedByLabel[band.label];
            return (
              <tr key={band.label}>
                <td>{band.label.replace(/_/g, " ")}</td>
                <td>{band.n}</td>
                <td>{num(band.metrics.total_points_mae, 1)}</td>
                <td>{num(r?.metrics.total_points_mae, 1)}</td>
                <td>{num(band.metrics.total_points_bias, 1)}</td>
                <td>{num(r?.metrics.total_points_bias, 1)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BySeasonTable({ original, revised }: { original: PoissonVariantReport; revised: PoissonVariantReport }) {
  const revisedByLabel = Object.fromEntries(revised.by_season.map((s) => [s.label, s]));
  return (
    <div className="segment-table-scroll">
      <table className="segment-table">
        <thead>
          <tr>
            <th>Season</th>
            <th>N</th>
            <th>Original total MAE</th>
            <th>Revised total MAE</th>
            <th>Original Brier</th>
            <th>Revised Brier</th>
          </tr>
        </thead>
        <tbody>
          {original.by_season.map((s) => {
            const r = revisedByLabel[s.label];
            const improved = r !== undefined && r.metrics.total_points_mae < s.metrics.total_points_mae;
            return (
              <tr key={s.label} className={s.label === "2021" ? "segment-table__highlight" : undefined}>
                <td>{s.label}</td>
                <td>{s.n}</td>
                <td>{num(s.metrics.total_points_mae, 1)}</td>
                <td className={improved ? "delta-positive" : "delta-negative"}>{num(r?.metrics.total_points_mae, 1)}</td>
                <td>{num(s.metrics.brier_score, 4)}</td>
                <td>{num(r?.metrics.brier_score, 4)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PoissonRevisionComparison({ comparison }: { comparison: PoissonRevisionComparisonData }) {
  const original2021 = comparison.original.season_2021_bands.find((b) => b.label === "full_season");
  const revised2021 = comparison.revised.season_2021_bands.find((b) => b.label === "full_season");

  return (
    <section className="backtest-panel">
      <h2>Poisson Season-Transition Revision</h2>
      <p className="hint">
        The 2021 anomaly (diagnosed earlier: 2020's COVID-shortened-quarter scoring depression carried too far into
        2021 via an unbounded expanding league-average baseline) fixed narrowly — the league-wide scoring baseline
        now uses a bounded rolling window (<code>league_window_games</code>) instead of expanding over the model's
        entire history, so a scoring shock ages out within roughly one window's worth of matches. "Original" is the
        currently-persisted, already-tuned config; "revised" is a fresh selection over the same tune/holdout grid,
        now including that window as a tunable parameter. Common matches evaluated: {comparison.common_match_count}.
      </p>

      <h3>Original vs revised config</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th></th>
              <th>rolling_window_games</th>
              <th>min_games_for_reliable_strength</th>
              <th>min_league_games_for_home_split</th>
              <th>league_window_games</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Original</td>
              <td>{comparison.original.config.rolling_window_games}</td>
              <td>{comparison.original.config.min_games_for_reliable_strength}</td>
              <td>{comparison.original.config.min_league_games_for_home_split}</td>
              <td>{comparison.original.config.league_window_games ?? "unbounded (None)"}</td>
            </tr>
            <tr className="segment-table__highlight">
              <td>Revised</td>
              <td>{comparison.revised.config.rolling_window_games}</td>
              <td>{comparison.revised.config.min_games_for_reliable_strength}</td>
              <td>{comparison.revised.config.min_league_games_for_home_split}</td>
              <td>{comparison.revised.config.league_window_games ?? "unbounded (None)"}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>Overall (evaluation period)</h3>
      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">Original total MAE</span>
          <span className="backtest-stat__value">{num(comparison.original.evaluation_metrics.total_points_mae, 1)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Revised total MAE</span>
          <span className="backtest-stat__value">{num(comparison.revised.evaluation_metrics.total_points_mae, 1)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Original margin MAE</span>
          <span className="backtest-stat__value">{num(comparison.original.evaluation_metrics.margin_mae, 1)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Revised margin MAE</span>
          <span className="backtest-stat__value">{num(comparison.revised.evaluation_metrics.margin_mae, 1)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Original winner Brier</span>
          <span className="backtest-stat__value">{num(comparison.original.evaluation_metrics.brier_score, 4)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Revised winner Brier</span>
          <span className="backtest-stat__value">{num(comparison.revised.evaluation_metrics.brier_score, 4)}</span>
        </div>
      </div>

      <h3>2021 specifically</h3>
      <p className="hint">
        Total-points MAE: <strong>{num(original2021?.metrics.total_points_mae, 1)} → {num(revised2021?.metrics.total_points_mae, 1)}</strong>
        {" "}({comparison.revised_beats_original_2021 ? "improved" : "not improved"}). Total-points bias:{" "}
        <strong>{num(original2021?.metrics.total_points_bias, 1)} → {num(revised2021?.metrics.total_points_bias, 1)}</strong>.
      </p>
      <RoundBandTable original={comparison.original.season_2021_bands} revised={comparison.revised.season_2021_bands} />

      <h3>Season-opening rounds, all evaluation seasons combined</h3>
      <p className="hint">Rounds 1-3, rounds 1-5, and the full season — is the fix a general season-opening improvement or specific to 2021?</p>
      <RoundBandTable original={comparison.original.early_season_bands} revised={comparison.revised.early_season_bands} />

      <h3>By season</h3>
      <p className="hint">2021 highlighted. A regression in any other season (most notably 2020 itself, which lost history depth under a bounded window) is reported here, not hidden.</p>
      <BySeasonTable original={comparison.original} revised={comparison.revised} />

      <h3>Interval calibration (evaluation period)</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th></th>
              <th>Total in 50% interval</th>
              <th>Total in 80% interval</th>
              <th>Margin in 50% interval</th>
              <th>Margin in 80% interval</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Original</td>
              <td>{pct(comparison.original.interval_coverage["50pct"]?.total_hit_rate)}</td>
              <td>{pct(comparison.original.interval_coverage["80pct"]?.total_hit_rate)}</td>
              <td>{pct(comparison.original.interval_coverage["50pct"]?.margin_hit_rate)}</td>
              <td>{pct(comparison.original.interval_coverage["80pct"]?.margin_hit_rate)}</td>
            </tr>
            <tr className="segment-table__highlight">
              <td>Revised</td>
              <td>{pct(comparison.revised.interval_coverage["50pct"]?.total_hit_rate)}</td>
              <td>{pct(comparison.revised.interval_coverage["80pct"]?.total_hit_rate)}</td>
              <td>{pct(comparison.revised.interval_coverage["50pct"]?.margin_hit_rate)}</td>
              <td>{pct(comparison.revised.interval_coverage["80pct"]?.margin_hit_rate)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className={`promotion-badge ${comparison.promotion.promote ? "promotion-badge--promote" : "promotion-badge--keep"}`}>
        {comparison.promotion.promote ? "Meets promotion bar — revised config recommended" : "Does not meet promotion bar — original config remains primary"}
      </div>
      <ul className="promotion-reasons">
        {comparison.promotion.reasons.map((r) => (
          <li key={r} className={r.startsWith("PASS") ? "promotion-reason--pass" : "promotion-reason--fail"}>
            {r}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default PoissonRevisionComparison;
