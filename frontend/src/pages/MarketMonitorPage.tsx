import { useEffect, useMemo, useState } from "react";
import { fetchAnomalies, fetchAnomalySummary, type AnomalyAlert, type AnomalySummary } from "../api/client";
import "./MarketMonitorPage.css";

const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1, info: 2 };

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`market-monitor__severity market-monitor__severity--${severity}`}>{severity}</span>;
}

function AlertRow({ alert, expanded, onToggle }: { alert: AnomalyAlert; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="market-monitor__row" onClick={onToggle}>
        <td>
          <SeverityBadge severity={alert.severity} />
        </td>
        <td className="market-monitor__type">{alert.alert_type}</td>
        <td>
          {alert.home_team} v {alert.away_team}
        </td>
        <td>{alert.player_name ?? alert.selection ?? "—"}</td>
        <td>
          {alert.market_type}
          {alert.threshold !== null ? ` ${alert.threshold}+` : ""}
        </td>
        <td>{fmtPct(alert.model_probability)}</td>
        <td>{fmtPct(alert.market_consensus_probability)}</td>
        <td>{alert.freshness ?? "—"}</td>
        <td>{new Date(alert.generated_at).toLocaleString()}</td>
      </tr>
      {expanded && (
        <tr className="market-monitor__drawer">
          <td colSpan={9}>
            <p className="market-monitor__detail">{alert.detail}</p>
            <div className="market-monitor__meta">
              <span>reason_code: {alert.reason_code}</span>
              <span>model_version: {alert.model_version ?? "—"}</span>
              <span>lineup_status: {alert.lineup_status ?? "—"}</span>
              {alert.context_state && <span>context: {alert.context_state}</span>}
            </div>
            {alert.model_risk_flags.length > 0 && (
              <div className="market-monitor__risk-flags">
                {alert.model_risk_flags.map((f, i) => (
                  <span key={i} className="market-monitor__risk-flag">
                    {f.code}: {f.description}
                  </span>
                ))}
              </div>
            )}
            {alert.bookmaker_prices.length > 0 && (
              <table className="market-monitor__books">
                <thead>
                  <tr>
                    <th>Bookmaker</th>
                    <th>Price</th>
                    <th>Recorded</th>
                    <th>Eligibility</th>
                  </tr>
                </thead>
                <tbody>
                  {alert.bookmaker_prices.map((b, i) => (
                    <tr key={i}>
                      <td>{b.bookmaker_name}</td>
                      <td>${b.price_decimal.toFixed(2)}</td>
                      <td>{new Date(b.recorded_at).toLocaleString()}</td>
                      <td>{b.eligibility}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function MarketMonitorPage() {
  const [summary, setSummary] = useState<AnomalySummary | null>(null);
  const [alerts, setAlerts] = useState<AnomalyAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [matchFilter, setMatchFilter] = useState<string>("");
  const [bookmakerFilter, setBookmakerFilter] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchAnomalySummary(), fetchAnomalies({ limit: 2000 })])
      .then(([s, a]) => {
        setSummary(s);
        setAlerts(a.alerts);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load market monitor data"))
      .finally(() => setLoading(false));
  }, []);

  const matches = useMemo(() => {
    const seen = new Map<number, string>();
    for (const a of alerts) seen.set(a.match_id, `${a.home_team} v ${a.away_team}`);
    return [...seen.entries()];
  }, [alerts]);

  const bookmakers = useMemo(() => {
    const seen = new Set<string>();
    for (const a of alerts) for (const b of a.bookmaker_prices) seen.add(b.bookmaker_name);
    return [...seen].sort();
  }, [alerts]);

  const filtered = useMemo(() => {
    return alerts
      .filter((a) => !typeFilter || a.alert_type === typeFilter)
      .filter((a) => !severityFilter || a.severity === severityFilter)
      .filter((a) => !matchFilter || String(a.match_id) === matchFilter)
      .filter((a) => !bookmakerFilter || a.bookmaker_prices.some((b) => b.bookmaker_name === bookmakerFilter))
      .sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  }, [alerts, typeFilter, severityFilter, matchFilter, bookmakerFilter]);

  return (
    <main className="market-monitor-page">
      <h1>Market Monitor — Trading QA</h1>
      <p className="subtitle">
        A neutral, read-only comparison of this engine's own pricing against real bookmaker markets — an
        independent second set of eyes for a trading desk, not a "find me a bet" surface. No probability or
        confidence shown anywhere on this page is adjusted by anything here.
      </p>

      {error && <p className="market-monitor-page__error">{error}</p>}
      {loading && <p>Loading…</p>}

      {summary && (
        <section className="market-monitor-section">
          <h2>Summary</h2>
          <p className="hint">
            {summary.total_anomalies} active anomalies across {summary.n_matches_scanned} scheduled match(es) with
            live pricing · generated {new Date(summary.generated_at).toLocaleString()}
          </p>
          <div className="market-monitor__summary-grid">
            <div>
              <h3>By type</h3>
              {summary.by_type.map((t) => (
                <div key={t.alert_type} className="market-monitor__summary-row">
                  <span>{t.alert_type}</span>
                  <span>{t.count}</span>
                </div>
              ))}
              {summary.by_type.length === 0 && <p className="hint">No active anomalies right now.</p>}
            </div>
            <div>
              <h3>By severity</h3>
              {summary.by_severity.map((s) => (
                <div key={s.severity} className="market-monitor__summary-row">
                  <SeverityBadge severity={s.severity} />
                  <span>{s.count}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <section className="market-monitor-section">
        <h2>Active anomalies</h2>
        <div className="market-monitor__filters">
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            <option value="">All types</option>
            {[...new Set(alerts.map((a) => a.alert_type))].sort().map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
            <option value="">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <select value={matchFilter} onChange={(e) => setMatchFilter(e.target.value)}>
            <option value="">All matches</option>
            {matches.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          <select value={bookmakerFilter} onChange={(e) => setBookmakerFilter(e.target.value)}>
            <option value="">All bookmakers</option>
            {bookmakers.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
          <span className="hint">
            {filtered.length} of {alerts.length}
          </span>
        </div>

        {filtered.length === 0 && !loading && <p className="empty-state">No anomalies match these filters.</p>}

        {filtered.length > 0 && (
          <div className="market-monitor__table-wrap">
            <table className="market-monitor__table">
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Type</th>
                  <th>Match</th>
                  <th>Player/Selection</th>
                  <th>Market</th>
                  <th>Model %</th>
                  <th>Consensus %</th>
                  <th>Freshness</th>
                  <th>Generated</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a, i) => {
                  const key = `${a.match_id}-${a.alert_type}-${a.reason_code}-${i}`;
                  return <AlertRow key={key} alert={a} expanded={expandedKey === key} onToggle={() => setExpandedKey(expandedKey === key ? null : key)} />;
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

export default MarketMonitorPage;
