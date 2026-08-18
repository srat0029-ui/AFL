import { useEffect, useState } from "react";
import "./DataFreshnessPanel.css";
import { ApiError, fetchDataFreshness, triggerRefresh, type DataFreshnessItem, type FreshnessStatus, type LiveCycleRun } from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";

const STATUS_LABELS: Record<FreshnessStatus, string> = {
  fresh: "Fresh",
  aging: "Aging",
  stale: "Stale",
  not_available: "Not available",
};

const RUN_STATUS_LABELS: Record<LiveCycleRun["overall_status"], string> = {
  ok: "Complete",
  partial: "Completed with warnings",
  blocked: "Blocked",
};

function FreshnessRow({ item }: { item: DataFreshnessItem }) {
  return (
    <div className="freshness-panel__row">
      <span className="freshness-panel__label">{item.label}</span>
      <span className={`freshness-badge freshness-badge--${item.status}`}>{STATUS_LABELS[item.status]}</span>
      <span className="freshness-panel__detail">
        {item.last_refreshed ? `Updated ${formatCompactDateTime(item.last_refreshed)}` : item.detail}
      </span>
    </div>
  );
}

function WhatChanged({ run }: { run: LiveCycleRun }) {
  const fixturesStep = run.steps.find((s) => s.step === "refresh_fixtures");
  const projectionsStep = run.steps.find((s) => s.step === "regenerate_projections");
  const rows: { label: string; detail: string }[] = [
    { label: "Fixtures", detail: fixturesStep?.detail ?? "not run" },
    { label: "Team odds", detail: `${run.team_odds_quotes_added} new quote(s)` },
    { label: "Player props", detail: `${run.quotes_added} new quote(s)` },
    { label: "Weather", detail: `${run.weather_snapshots_added} new snapshot(s)` },
    { label: "Projections", detail: projectionsStep?.detail ?? "not run" },
    { label: "Observations", detail: `${run.observations_added} new` },
    { label: "Settlements", detail: `${run.observations_settled} settled` },
  ];
  return (
    <div className="freshness-panel__result">
      <div className="freshness-panel__result-header">
        <span className={`freshness-panel__run-badge freshness-panel__run-badge--${run.overall_status}`}>{RUN_STATUS_LABELS[run.overall_status]}</span>
        <span className="hint">{formatCompactDateTime(run.run_at)}</span>
      </div>
      <ul className="freshness-panel__changes">
        {rows.map((r) => (
          <li key={r.label}>
            <strong>{r.label}:</strong> {r.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DataFreshnessPanel() {
  const [items, setItems] = useState<DataFreshnessItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<LiveCycleRun | null>(null);

  function loadFreshness() {
    setLoading(true);
    fetchDataFreshness()
      .then((r) => setItems(r.items))
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load data freshness"))
      .finally(() => setLoading(false));
  }

  useEffect(loadFreshness, []);

  function handleRefresh() {
    setRefreshing(true);
    setRefreshError(null);
    triggerRefresh()
      .then((run) => {
        setLastRun(run);
        loadFreshness();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409) {
          setRefreshError("A refresh is already running — please wait for it to finish.");
        } else {
          setRefreshError(err instanceof Error ? err.message : "Refresh failed");
        }
      })
      .finally(() => setRefreshing(false));
  }

  return (
    <section className="card freshness-panel">
      <div className="freshness-panel__header">
        <h2>Data Freshness</h2>
        <button className="btn freshness-panel__refresh-btn" onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh Data"}
        </button>
      </div>

      {refreshing && <p className="loading-state">Refreshing fixtures, odds, weather, and projections — this can take up to a minute…</p>}
      {refreshError && <div className="error-banner">{refreshError}</div>}

      {loading && <p className="loading-state">Loading…</p>}
      {loadError && <div className="error-banner">{loadError}</div>}

      {!loading && !loadError && items && (
        <div className="freshness-panel__grid">
          {items.map((item) => (
            <FreshnessRow key={item.category} item={item} />
          ))}
        </div>
      )}

      {lastRun && <WhatChanged run={lastRun} />}
    </section>
  );
}

export default DataFreshnessPanel;
