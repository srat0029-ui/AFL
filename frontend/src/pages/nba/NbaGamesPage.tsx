import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchNbaOutlookGames, type NbaOutlookGames } from "../../api/client";
import EmptyState from "../../components/ui/EmptyState";
import PageHeader from "../../components/ui/PageHeader";
import Skeleton from "../../components/ui/Skeleton";
import { formatAge, minutesSince, windowStateDisplay } from "../../features/nbaOutlook";
import { dayGroupKey, dayGroupLabel, formatClockTime, formatCompactDateTime, formatCountdown } from "../../lib/datetime";
import "./NbaPages.css";

type LoadState = { status: "loading" } | { status: "ready"; data: NbaOutlookGames; loadedAt: Date } | { status: "error"; message: string };

/** Upcoming and just-played NBA games with the state of each frozen V1.5
 * outlook window. Reads stored data only: the Refresh button re-reads it,
 * it never triggers a provider request or the AFL live cycle. */
function NbaGamesPage() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    setRefreshing(true);
    return fetchNbaOutlookGames(signal)
      .then((data) => setState({ status: "ready", data, loadedAt: new Date() }))
      .catch((err) => {
        if (signal?.aborted) return;
        setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
      })
      .finally(() => setRefreshing(false));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const refreshButton = (
    <button type="button" className="btn" onClick={() => load()} disabled={refreshing} title="Re-read the stored NBA outlooks. Does not fetch odds or run any collector.">
      {refreshing ? "Refreshing…" : "Refresh"}
    </button>
  );

  return (
    <div className="nba-page">
      <PageHeader
        eyebrow="NBA"
        title="Games and rotation outlooks"
        description="Frozen V1.5 predictions — chance of playing, of playing 10+ minutes, and expected minutes if he plays — taken 24 h, 4 h, 1 h and 30 min before each tip-off."
        actions={refreshButton}
      />

      {state.status === "loading" && <Skeleton height="10rem" />}
      {state.status === "error" && <EmptyState title="Could not load NBA games" description={state.message} />}

      {state.status === "ready" && (
        <>
          <CollectorStatus data={state.data} loadedAt={state.loadedAt} />
          {state.data.games.length === 0 ? (
            <EmptyState
              title="No games in this window"
              description={`No competitive NBA games tip off between ${state.data.window.hours_back} h ago and ${state.data.window.hours_ahead} h from now.`}
            />
          ) : (
            <GameList data={state.data} />
          )}
        </>
      )}
    </div>
  );
}

function CollectorStatus({ data, loadedAt }: { data: NbaOutlookGames; loadedAt: Date }) {
  const ev = data.live_evidence;
  const lastRun = minutesSince(ev.latest_run_at);
  return (
    <section className={`card nba-collector ${ev.healthy ? "" : "nba-collector--problem"}`}>
      <div>
        <span className={`chip ${ev.healthy ? "chip--success" : "chip--danger"}`}>{ev.healthy ? "Evidence collector healthy" : "Evidence collector problem"}</span>
        <span className="nba-muted"> Last cycle {formatAge(lastRun)}{ev.latest_run_status ? ` (${ev.latest_run_status})` : ""}.</span>
      </div>
      {ev.problems.length > 0 && (
        <ul className="nba-problems">
          {ev.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="nba-muted nba-small">Loaded {formatClockTime(loadedAt.toISOString())} · data as of {formatCompactDateTime(data.as_of)}</div>
    </section>
  );
}

function GameList({ data }: { data: NbaOutlookGames }) {
  const groups = new Map<string, typeof data.games>();
  for (const g of data.games) {
    const key = dayGroupKey(g.tipoff);
    groups.set(key, [...(groups.get(key) ?? []), g]);
  }
  return (
    <>
      {[...groups.entries()].map(([key, games]) => (
        <section key={key} className="card nba-day">
          <h2 className="section-title">{dayGroupLabel(games[0].tipoff)}</h2>
          <table className="data-table nba-games-table">
            <thead>
              <tr>
                <th>Tip-off</th>
                <th>Game</th>
                <th>Status</th>
                {data.games[0].windows.map((w) => (
                  <th key={w.label}>{w.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {games.map((g) => (
                <tr key={g.game_id}>
                  <td>
                    {formatClockTime(g.tipoff)}
                    {g.status === "scheduled" && <div className="nba-muted nba-small">{formatCountdown(g.tipoff)}</div>}
                  </td>
                  <td>
                    <Link to={`/nba/games/${g.game_id}`} className="nba-matchup" title={`${g.away.name} at ${g.home.name}`}>
                      {g.away.abbreviation} @ {g.home.abbreviation}
                    </Link>
                  </td>
                  <td className="nba-muted">{g.status.replace("_", " ")}</td>
                  {g.windows.map((w) => {
                    const d = windowStateDisplay(w.state);
                    return (
                      <td key={w.label}>
                        <span className={`chip chip--${d.variant}`} title={d.description}>
                          {d.label}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}

export default NbaGamesPage;
