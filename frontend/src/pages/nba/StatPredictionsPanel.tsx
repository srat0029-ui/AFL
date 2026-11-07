import { useEffect, useState } from "react";
import { fetchNbaStatPredictions, type NbaStatName, type NbaStatPlayerRow, type NbaStatPredictionGame } from "../../api/client";
import { formatMinutes, formatProbability } from "../../features/nbaOutlook";
import { BASELINE_LABELS, STAT_NAMES, STAT_SHORT, formatStatMean, formatStatRange, labelsWithRows, validationText } from "../../features/nbaStatPredictions";
import { formatCompactDateTime } from "../../lib/datetime";

type LoadState = { status: "loading" } | { status: "ready"; data: NbaStatPredictionGame } | { status: "error"; message: string };

/** Research only: the frozen Stat Model V1 predictions for one game. Shows
 * what was stored before tip-off and, once final, what happened. No odds,
 * lines, edges or recommendations. */
function StatPredictionsPanel({ gameId, activeLabel, reloadKey }: { gameId: number; activeLabel: string; reloadKey: number }) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [baselinesFor, setBaselinesFor] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchNbaStatPredictions(gameId, controller.signal)
      .then((data) => setState({ status: "ready", data }))
      .catch((err) => {
        if (!controller.signal.aborted) setState({ status: "error", message: err instanceof Error ? err.message : "Unknown error" });
      });
    return () => controller.abort();
  }, [gameId, reloadKey]);

  if (state.status === "loading") return null;
  if (state.status === "error") {
    return (
      <section className="card nba-research">
        <ResearchHead />
        <p className="nba-muted">Could not load frozen stat predictions: {state.message}</p>
      </section>
    );
  }

  const data = state.data;
  const available = labelsWithRows(data.labels, data.windows);
  const label = available.includes(activeLabel) ? activeLabel : available[available.length - 1];
  const window = data.windows.find((w) => w.label === label);
  const final = data.status === "final";

  return (
    <section className="card nba-research">
      <ResearchHead version={data.model_version} />
      {available.length === 0 ? (
        <p className="nba-muted">
          No stat predictions were frozen for this game. They are saved only at T-4h and T-1h, from 7 Nov 2026 onwards, and a missed window is never filled in later.
        </p>
      ) : (
        <>
          <dl className="nba-window__meta">
            <dt>Window</dt>
            <dd>
              {label}
              {label !== activeLabel && <span className="nba-muted"> (no stat predictions at {activeLabel})</span>}
            </dd>
            {window && (
              <>
                <dt>Information cutoff</dt>
                <dd>{formatCompactDateTime(window.information_cutoff)}</dd>
                <dt>Frozen at</dt>
                <dd>{formatCompactDateTime(window.generated_at)}</dd>
              </>
            )}
            {STAT_NAMES.map((s) => (
              <StatusRow key={s} stat={s} data={data} />
            ))}
          </dl>
          {[data.away, data.home].map((team) => (
            <TeamStats key={team.id} name={team.name ?? "Team"} players={team.players} label={label} final={final} baselinesFor={baselinesFor} onToggle={setBaselinesFor} />
          ))}
          <p className="nba-muted nba-small">
            Means are minutes × a shrunk per-minute rate, using only box scores known at the cutoff, and are never edited after freezing. The 80% range is the stored
            distribution's 10th–90th percentile. Points show no range because its distribution failed the offline check. These are research numbers for checking the model,
            not betting advice. No conclusions until 300 final games have T-1h rows.
          </p>
        </>
      )}
    </section>
  );
}

function ResearchHead({ version }: { version?: string }) {
  return (
    <div className="nba-window__head">
      <h2 className="section-title">Stat predictions</h2>
      <span className="chip chip--accent">Research only</span>
      {version && <span className="nba-muted nba-small">{version}</span>}
    </div>
  );
}

function StatusRow({ stat, data }: { stat: NbaStatName; data: NbaStatPredictionGame }) {
  const v = data.validation[stat];
  return (
    <>
      <dt>{stat[0].toUpperCase() + stat.slice(1)}</dt>
      <dd>
        {validationText(v)}
        {v && !v.calibrated && <span className="chip chip--warning nba-chip-inline">Uncalibrated</span>}
      </dd>
    </>
  );
}

function TeamStats({
  name,
  players,
  label,
  final,
  baselinesFor,
  onToggle,
}: {
  name: string;
  players: NbaStatPlayerRow[];
  label: string;
  final: boolean;
  baselinesFor: number | null;
  onToggle: (id: number | null) => void;
}) {
  const shown = players.filter((p) => label in p.predictions);
  if (shown.length === 0) return null;
  return (
    <div className="nba-research__team">
      <h3 className="nba-research__team-name">{name}</h3>
      <div className="nba-table-wrap">
        <table className="data-table nba-players-table">
          <thead>
            <tr>
              <th>Player</th>
              <th className="num" title="Frozen V1.5 expected minutes if he plays">Min</th>
              <th className="num" title="Frozen V1.5 probability he plays">P(play)</th>
              {STAT_NAMES.map((s) => (
                <th key={s} className="num" title={s === "points" ? "Mean only: the points distribution failed its offline check" : "Mean, with the 80% range below"}>
                  {STAT_SHORT[s]}
                </th>
              ))}
              {final && <th className="num">Result</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <PlayerStatRow key={p.player_id} row={p} label={label} final={final} open={baselinesFor === p.player_id} onToggle={onToggle} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PlayerStatRow({ row, label, final, open, onToggle }: { row: NbaStatPlayerRow; label: string; final: boolean; open: boolean; onToggle: (id: number | null) => void }) {
  const pred = row.predictions[label];
  const columns = 4 + STAT_NAMES.length + (final ? 1 : 0);
  return (
    <>
      <tr>
        <td>
          {row.name}
          {row.position && <span className="nba-muted"> · {row.position}</span>}
        </td>
        <td className="num">{formatMinutes(pred.expected_minutes)}</td>
        <td className="num">{formatProbability(pred.p_play)}</td>
        {STAT_NAMES.map((s) => {
          const range = formatStatRange(pred.stats[s]);
          return (
            <td key={s} className="num">
              {formatStatMean(pred.stats[s]?.mean)}
              {range && <div className="nba-muted nba-small">{range}</div>}
            </td>
          );
        })}
        {final && (
          <td className="num">
            {row.result == null ? "—" : !row.result.played ? <span className="nba-muted">DNP</span> : STAT_NAMES.map((s) => row.result?.[s] ?? "—").join(" / ")}
          </td>
        )}
        <td>
          <button type="button" className="btn btn--small" onClick={() => onToggle(open ? null : row.player_id)} aria-expanded={open}>
            {open ? "Hide" : "Baselines"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="nba-research__baselines">
          <td colSpan={columns}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Frozen alongside</th>
                  {STAT_NAMES.map((s) => (
                    <th key={s} className="num">
                      {STAT_SHORT[s]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Stat V1</td>
                  {STAT_NAMES.map((s) => (
                    <td key={s} className="num">
                      {formatStatMean(pred.stats[s]?.mean)}
                    </td>
                  ))}
                </tr>
                {Object.keys(BASELINE_LABELS).map((b) => (
                  <tr key={b}>
                    <td className="nba-muted">{BASELINE_LABELS[b]}</td>
                    {STAT_NAMES.map((s) => (
                      <td key={s} className="num nba-muted">
                        {formatStatMean(pred.stats[s]?.baselines[b])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

export default StatPredictionsPanel;
