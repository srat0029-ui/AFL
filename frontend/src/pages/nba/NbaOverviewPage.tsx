import { useEffect, useState } from "react";
import { fetchNbaStatus, type NbaStatus } from "../../api/client";
import EmptyState from "../../components/ui/EmptyState";
import PageHeader from "../../components/ui/PageHeader";
import Skeleton from "../../components/ui/Skeleton";
import { StatTile, StatTileRow } from "../../components/ui/StatTile";
import { formatCompactDateTime } from "../../lib/datetime";

const MARKET_LABELS: Record<string, string> = {
  player_points: "Points",
  player_rebounds: "Rebounds",
  player_assists: "Assists",
};

type LoadState = { status: "loading" } | { status: "ready"; data: NbaStatus } | { status: "error"; message: string };

/** The NBA entry point. Shows only what is actually in the NBA tables — row
 * counts and how far predictions have progressed through freeze / close /
 * settle. No projections, prices or recommendations are rendered here until
 * real ones exist. */
function NbaOverviewPage() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchNbaStatus(controller.signal)
      .then((data) => setState({ status: "ready", data }))
      .catch((err) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <div>
      <PageHeader
        eyebrow="NBA"
        title="NBA player props"
        description="Points, rebounds and assists singles, tracked prospectively: every prediction is frozen before tip-off, compared with the closing line, then settled against the box score."
      />

      {state.status === "loading" && <Skeleton height="8rem" />}

      {state.status === "error" && <EmptyState title="Could not load NBA status" description={state.message} />}

      {state.status === "ready" && (
        <>
          <StatTileRow>
            <StatTile label="Predictions frozen" value={state.data.predictions_frozen.toLocaleString()} meta="Before tip-off" />
            <StatTile label="With closing line" value={state.data.predictions_with_closing_line.toLocaleString()} meta="Market closed" />
            <StatTile label="Settled" value={state.data.predictions_settled.toLocaleString()} meta="Against the box score" />
          </StatTileRow>

          {state.data.datasets.every((d) => d.rows === 0) && (
            <EmptyState
              title="No NBA data yet"
              description="The NBA data model and prospective pipeline are in place, but nothing has been ingested and no model has been built. This page will fill in as real data arrives."
            />
          )}

          <section className="card" style={{ marginTop: "1rem" }}>
            <h2 className="section-title">Data</h2>
            <p className="page-subtitle">Markets covered: {state.data.markets.map((m) => MARKET_LABELS[m] ?? m).join(", ")}.</p>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th className="num">Rows</th>
                  <th>Most recent</th>
                </tr>
              </thead>
              <tbody>
                {state.data.datasets.map((d) => (
                  <tr key={d.key}>
                    <td>{d.label}</td>
                    <td className="num">{d.rows.toLocaleString()}</td>
                    <td>{d.latest_at ? formatCompactDateTime(d.latest_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}

export default NbaOverviewPage;
