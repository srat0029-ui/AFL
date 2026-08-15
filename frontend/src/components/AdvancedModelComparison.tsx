import type { LogisticComparisonOverview, LogisticVariantReport } from "../api/client";

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function num(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

const VARIANT_LABELS: Record<string, string> = {
  stats_only: "Logistic (stats only)",
  stats_plus_elo: "Logistic (stats + Elo)",
};

function CoefficientBars({ coefficients }: { coefficients: Record<string, number> }) {
  const entries = Object.entries(coefficients).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const maxAbs = Math.max(0.001, ...entries.map(([, v]) => Math.abs(v)));

  return (
    <div className="coef-bars">
      {entries.map(([name, value]) => {
        const widthPct = (Math.abs(value) / maxAbs) * 50;
        return (
          <div className="coef-bar-row" key={name}>
            <span className="coef-bar-label">{name}</span>
            <div className="coef-bar-track">
              <div className="coef-bar-zero" />
              <div
                className={`coef-bar-fill ${value >= 0 ? "coef-bar-fill--positive" : "coef-bar-fill--negative"}`}
                style={{ width: `${widthPct}%`, [value >= 0 ? "left" : "right"]: "50%" } as React.CSSProperties}
              />
            </div>
            <span className="coef-bar-value">{value >= 0 ? "+" : ""}{value.toFixed(3)}</span>
          </div>
        );
      })}
    </div>
  );
}

function VariantSection({ variant, elo }: { variant: LogisticVariantReport; elo: { brier_score: number } }) {
  const b = variant.bootstrap_vs_elo;
  return (
    <div className="logistic-variant">
      <h3>{VARIANT_LABELS[variant.variant] ?? variant.variant}</h3>
      <p className="hint">
        {variant.feature_names.length} features · C={variant.C} · calibration: {variant.calibration_method}
      </p>

      <div className="backtest-overall">
        <div className="backtest-stat">
          <span className="backtest-stat__label">Brier score</span>
          <span className="backtest-stat__value">{num(variant.brier_score)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Log loss</span>
          <span className="backtest-stat__value">{num(variant.log_loss)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">Accuracy</span>
          <span className="backtest-stat__value">{pct(variant.accuracy)}</span>
        </div>
        <div className="backtest-stat">
          <span className="backtest-stat__label">ECE</span>
          <span className="backtest-stat__value">{num(variant.calibration_ece)}</span>
        </div>
      </div>

      <p className="hint" style={{ marginTop: "0.9rem" }}>
        Brier improvement vs Elo ({num(elo.brier_score)}): <strong>{b.point_estimate >= 0 ? "+" : ""}{num(b.point_estimate)}</strong>
        {" "}— 95% bootstrap interval [{num(b.ci_low)}, {num(b.ci_high)}]
        {b.excludes_zero ? " (distinguishable from noise)" : " (not distinguishable from noise)"}
      </p>

      <div className={`promotion-badge ${variant.promotion.promote ? "promotion-badge--promote" : "promotion-badge--keep"}`}>
        {variant.promotion.promote ? "Meets promotion bar" : "Does not meet promotion bar — Elo remains primary"}
      </div>
      <ul className="promotion-reasons">
        {variant.promotion.reasons.map((r) => (
          <li key={r} className={r.startsWith("PASS") ? "promotion-reason--pass" : "promotion-reason--fail"}>
            {r}
          </li>
        ))}
      </ul>

      <h4>Feature-group ablation (Elo alone vs Elo + group)</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Group</th>
              <th>Brier</th>
              <th>vs Elo alone</th>
            </tr>
          </thead>
          <tbody>
            {variant.feature_group_ablation.map((a) => (
              <tr key={a.label}>
                <td>{a.label}</td>
                <td>{num(a.brier_score)}</td>
                <td className={a.brier_vs_elo_alone !== null && a.brier_vs_elo_alone < 0 ? "delta-positive" : "delta-negative"}>
                  {a.brier_vs_elo_alone === null ? "—" : `${a.brier_vs_elo_alone >= 0 ? "+" : ""}${num(a.brier_vs_elo_alone)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Standardized coefficients</h4>
      <CoefficientBars coefficients={variant.standardized_coefficients} />

      <h4>By season</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Season</th>
              <th>N</th>
              <th>Brier</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {variant.by_season.map((s) => (
              <tr key={s.label}>
                <td>{s.label}</td>
                <td>{s.n}</td>
                <td>{num(s.metrics.brier_score)}</td>
                <td>{num(s.metrics.log_loss)}</td>
                <td>{pct(s.metrics.accuracy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Disagreement vs Elo</h4>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Disagreement</th>
              <th>N</th>
              <th>Elo Brier</th>
              <th>Logistic Brier</th>
              <th>Home win rate</th>
            </tr>
          </thead>
          <tbody>
            {variant.disagreement_vs_elo.disagreement_buckets.map((bucket) => (
              <tr key={bucket.label}>
                <td>{bucket.label}</td>
                <td>{bucket.n}</td>
                <td>{num(bucket.elo_metrics.brier_score)}</td>
                <td>{num(bucket.logistic_metrics.brier_score)}</td>
                <td>{pct(bucket.actual_home_win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AdvancedModelComparison({ overview }: { overview: LogisticComparisonOverview }) {
  return (
    <section className="backtest-panel">
      <h2>Advanced Model Comparison</h2>
      <p className="hint">
        Does richer AFL team statistics (clearances, inside 50s, contested possession, tackles, recent form)
        improve on Elo's already-strong match-winner probability? A regularised logistic regression, tested both
        with and without Elo as an input feature, evaluated on the identical {overview.n_eval}-match set
        ({overview.evaluation_start_year}–{overview.evaluation_end_year}) as every other model on this page.
      </p>

      <h3>Everything on the same match set</h3>
      <div className="segment-table-scroll">
        <table className="segment-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>N</th>
              <th>Brier</th>
              <th>Log loss</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {overview.baselines.map((row) => (
              <tr key={row.name}>
                <td>{row.name}</td>
                <td>{row.n}</td>
                <td>{num(row.brier_score)}</td>
                <td>{num(row.log_loss)}</td>
                <td>{pct(row.accuracy)}</td>
              </tr>
            ))}
            <tr>
              <td>elo</td>
              <td>{overview.elo.n}</td>
              <td>{num(overview.elo.brier_score)}</td>
              <td>{num(overview.elo.log_loss)}</td>
              <td>{pct(overview.elo.accuracy)}</td>
            </tr>
            <tr>
              <td>poisson</td>
              <td>{overview.poisson.n}</td>
              <td>{num(overview.poisson.brier_score)}</td>
              <td>{num(overview.poisson.log_loss)}</td>
              <td>{pct(overview.poisson.accuracy)}</td>
            </tr>
            <tr className="segment-table__highlight">
              <td>logistic (stats only)</td>
              <td>{overview.stats_only.n_eval}</td>
              <td>{num(overview.stats_only.brier_score)}</td>
              <td>{num(overview.stats_only.log_loss)}</td>
              <td>{pct(overview.stats_only.accuracy)}</td>
            </tr>
            <tr className="segment-table__highlight">
              <td>logistic (stats + Elo)</td>
              <td>{overview.stats_plus_elo.n_eval}</td>
              <td>{num(overview.stats_plus_elo.brier_score)}</td>
              <td>{num(overview.stats_plus_elo.log_loss)}</td>
              <td>{pct(overview.stats_plus_elo.accuracy)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <VariantSection variant={overview.stats_only} elo={overview.elo} />
      <VariantSection variant={overview.stats_plus_elo} elo={overview.elo} />
    </section>
  );
}

export default AdvancedModelComparison;
