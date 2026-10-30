import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchNbaOutlookGame, type NbaOutlookGameDetail, type NbaPlayerOutlookRow, type NbaWindowDetail } from "../../api/client";
import EmptyState from "../../components/ui/EmptyState";
import PageHeader from "../../components/ui/PageHeader";
import Skeleton from "../../components/ui/Skeleton";
import Tabs from "../../components/ui/Tabs";
import {
  availabilityChanged,
  availabilityDisplay,
  defaultLabel,
  formatAge,
  formatMinutes,
  formatMinutesRange,
  formatProbability,
  windowStateDisplay,
} from "../../features/nbaOutlook";
import { formatClockTime, formatCompactDateTime, formatFullDateTime } from "../../lib/datetime";
import "./NbaPages.css";

type LoadState = { status: "loading" } | { status: "ready"; data: NbaOutlookGameDetail; loadedAt: Date } | { status: "error"; message: string };

const TIER_LABELS: Record<string, string> = {
  likely_rotation: "Likely rotation",
  likely_active: "Likely active",
  uncertain: "Uncertain",
  unlikely: "Unlikely",
};

/** One game's frozen V1.5 outlooks, window by window, for both teams. */
function NbaGamePage() {
  const { gameId } = useParams();
  const id = Number(gameId);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [label, setLabel] = useState<string | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      setRefreshing(true);
      return fetchNbaOutlookGame(id, signal)
        .then((data) => setState({ status: "ready", data, loadedAt: new Date() }))
        .catch((err) => {
          if (signal?.aborted) return;
          setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
        })
        .finally(() => setRefreshing(false));
    },
    [id],
  );

  useEffect(() => {
    if (!Number.isFinite(id)) {
      setState({ status: "error", message: "Not a valid game id." });
      return;
    }
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [id, load]);

  if (state.status === "loading") return <Skeleton height="16rem" />;
  if (state.status === "error") {
    return (
      <div className="nba-page">
        <EmptyState title="Could not load this game" description={state.message} action={<Link to="/nba/games">Back to games</Link>} />
      </div>
    );
  }

  const data = state.data;
  const rows = [...data.home.players, ...data.away.players];
  const active = label ?? defaultLabel(data.labels, rows);
  const window = data.windows.find((w) => w.label === active);

  return (
    <div className="nba-page">
      <PageHeader
        eyebrow="NBA game"
        title={`${data.away.name} at ${data.home.name}`}
        description={
          <>
            Tip-off {formatFullDateTime(data.tipoff)} · {data.status.replace("_", " ")}
            {data.status === "final" && data.box_score_state ? ` · box score ${data.box_score_state}` : ""}
          </>
        }
        actions={
          <>
            <Link to="/nba/games" className="btn">
              All games
            </Link>
            <button type="button" className="btn" onClick={() => load()} disabled={refreshing} title="Re-read the stored outlooks. Does not fetch odds or run any collector.">
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </>
        }
      />

      <Tabs
        tabs={data.windows.map((w) => ({ value: w.label, label: `${w.label} · ${windowStateDisplay(w.state).label}` }))}
        value={active}
        onChange={setLabel}
      />

      {window && <WindowPanel window={window} teamAbbr={{ [String(data.home.id)]: data.home.abbreviation, [String(data.away.id)]: data.away.abbreviation }} />}

      {rows.length === 0 ? (
        <EmptyState title="No outlooks for this game yet" description="Players appear once the first window (T-24h) has been frozen for this tip-off." />
      ) : (
        <>
          <TeamTable team={data.away} label={active} final={data.status === "final"} />
          <TeamTable team={data.home} label={active} final={data.status === "final"} />
        </>
      )}

      <p className="nba-muted nba-small">
        Loaded {formatClockTime(state.loadedAt.toISOString())}. Outlooks are V1.5 and were frozen at each window's cutoff; they are never edited. "Now" shows the injury feed as
        of this page load — evidence the frozen outlook did not have. "Not listed" means the feed has no entry, not that the player is healthy. V1.5 does not use injury
        evidence in its probabilities.
      </p>
    </div>
  );
}

function freshnessLine(freshness: Record<string, unknown> | undefined, teamAbbr: Record<string, string>): { key: string; text: string; cls: string }[] {
  if (!freshness) return [];
  const out: { key: string; text: string; cls: string }[] = [];
  const add = (key: string, name: string, entry: unknown) => {
    const e = entry as { age_minutes?: number | null; class?: string } | undefined;
    if (!e || typeof e !== "object") return;
    out.push({ key, text: `${name}: ${e.class ?? "?"}${e.age_minutes != null ? ` (${formatAge(e.age_minutes)})` : ""}`, cls: e.class ?? "" });
  };
  add("injury_feed", "Injury feed", freshness.injury_feed);
  add("schedule", "Schedule", freshness.schedule);
  const rosters = freshness.roster as Record<string, unknown> | undefined;
  if (rosters) Object.entries(rosters).forEach(([team, e]) => add(`roster-${team}`, `${teamAbbr[team] ?? "Team"} roster`, e));
  const box = freshness.box_scores as { class?: string; missing_box_scores?: number[] } | undefined;
  if (box) out.push({ key: "box", text: `Recent box scores: ${box.class ?? "?"}${box.missing_box_scores?.length ? ` (${box.missing_box_scores.length} missing)` : ""}`, cls: box.class ?? "" });
  return out;
}

function WindowPanel({ window, teamAbbr }: { window: NbaWindowDetail; teamAbbr: Record<string, string> }) {
  const d = windowStateDisplay(window.state);
  const versions = Object.entries(window.model_versions ?? {});
  return (
    <section className="card nba-window">
      <div className="nba-window__head">
        <span className={`chip chip--${d.variant}`}>{d.label}</span>
        <span className="nba-muted">{d.description}</span>
      </div>
      <dl className="nba-window__meta">
        <dt>Target cutoff</dt>
        <dd>{formatCompactDateTime(window.target_cutoff)}</dd>
        {window.generated_at && (
          <>
            <dt>Frozen at</dt>
            <dd>{formatCompactDateTime(window.generated_at)}</dd>
          </>
        )}
        {window.reason && (
          <>
            <dt>Reason</dt>
            <dd>{window.reason}</dd>
          </>
        )}
        {versions.length > 0 && (
          <>
            <dt>Models</dt>
            <dd>{versions.map(([k, v]) => `${k}: ${v}`).join(" · ")}</dd>
          </>
        )}
      </dl>
      {window.snapshot_id != null && (
        <div className="nba-freshness">
          {freshnessLine(window.evidence_freshness, teamAbbr).map((f) => (
            <span key={f.key} className={`chip ${f.cls === "fresh" ? "chip--success" : f.cls === "stale" ? "chip--warning" : f.cls ? "chip--danger" : "chip--neutral"}`}>
              {f.text}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function TeamTable({ team, label, final }: { team: NbaOutlookGameDetail["home"]; label: string; final: boolean }) {
  const shown = team.players.filter((p) => label in p.outlooks);
  const missing = team.players.length - shown.length;
  return (
    <section className="card nba-team">
      <h2 className="section-title">
        {team.name} <span className="nba-muted">({team.abbreviation})</span>
      </h2>
      {shown.length === 0 ? (
        <p className="nba-muted">No outlooks frozen for this team at {label}.</p>
      ) : (
        <div className="nba-table-wrap">
          <table className="data-table nba-players-table">
            <thead>
              <tr>
                <th>Player</th>
                <th className="num" title="Probability he enters the game">P(play)</th>
                <th className="num" title="Probability he plays at least 10 minutes">P(10+ min)</th>
                <th className="num" title="Expected minutes if he plays, with the 10th–90th percentile range">Minutes if plays</th>
                <th>Tier</th>
                <th title="Injury feed as stored with this outlook">Feed at cutoff</th>
                <th title="Injury feed as of this page load">Feed now</th>
                {final && <th className="num">Result</th>}
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <PlayerRow key={p.player_id} row={p} label={label} final={final} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {missing > 0 && <p className="nba-muted nba-small">{missing} player(s) have outlooks only in other windows.</p>}
    </section>
  );
}

function PlayerRow({ row, label, final }: { row: NbaPlayerOutlookRow; label: string; final: boolean }) {
  const o = row.outlooks[label];
  const seen = availabilityDisplay(o.availability_evidence);
  const now = availabilityDisplay(row.current_availability);
  const changed = availabilityChanged(o.availability_evidence, row.current_availability);
  const range = formatMinutesRange(o.quantiles);
  return (
    <tr>
      <td>
        {row.name}
        {row.position && <span className="nba-muted"> · {row.position}</span>}
      </td>
      <td className="num">{formatProbability(o.p_play)}</td>
      <td className="num">{formatProbability(o.p_rotation)}</td>
      <td className="num">
        {o.expected_minutes_if_plays == null ? <span className="nba-muted" title="No playing history in the modelled window: not estimated">no history</span> : formatMinutes(o.expected_minutes_if_plays)}
        {range && <div className="nba-muted nba-small">{range}</div>}
      </td>
      <td>{TIER_LABELS[o.tier] ?? o.tier}</td>
      <td>
        <span className={`chip chip--${seen.variant}`}>{seen.label}</span>
      </td>
      <td>
        <span className={`chip chip--${now.variant}`}>{now.label}</span>
        {changed && <div className="nba-changed nba-small">changed since cutoff</div>}
      </td>
      {final && (
        <td className="num">
          {row.result == null ? "—" : !row.result.dressed ? <span className="nba-muted">did not dress</span> : !row.result.played ? <span className="nba-muted">DNP</span> : `${formatMinutes(row.result.minutes)} min`}
        </td>
      )}
    </tr>
  );
}

export default NbaGamePage;
