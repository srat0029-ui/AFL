import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import "./ProjectionTable.css";
import type { ConfidenceTierLive, DisposalProjection, GoalProjection, SelectionStatus } from "../api/client";
import { formatCompactDateTime, formatShortDate } from "../lib/datetime";

const CONFIDENCE_LABELS: Record<ConfidenceTierLive, string> = {
  higher_confidence: "Higher",
  moderate_confidence: "Moderate",
  lower_confidence: "Lower",
  insufficient_history: "Insufficient history",
};

const SELECTION_LABELS: Record<SelectionStatus, string> = {
  placeholder: "Placeholder",
  named_in_squad: "Named in squad",
  confirmed_selected: "Confirmed",
  emergency: "Emergency",
  substitute: "Substitute",
  confirmed_out: "Confirmed out",
  uncertain: "Uncertain",
};

const DISPOSAL_INPUT_LABELS: Record<string, string> = {
  disposals_last3_avg: "Last 3 avg",
  disposals_last5_avg: "Last 5 avg",
  disposals_last10_avg: "Last 10 avg",
  disposals_season_avg: "Season avg",
  disposals_career_avg: "Career avg",
  disposals_ewma: "EWMA",
  disposals_last5_std: "Last 5 std dev",
  opponent_disposals_conceded_avg: "Opponent disposals conceded",
  opponent_expected_score: "Opponent expected score",
  tog_last5_avg: "Recent time on ground",
};

const GOAL_INPUT_LABELS: Record<string, string> = {
  goals_last3_avg: "Last 3 avg",
  goals_last5_avg: "Last 5 avg",
  goals_career_avg: "Career avg",
  goals_ewma: "EWMA",
  zero_goal_rate_last10: "Zero-goal rate (last 10)",
  marks_inside_50_last5_avg: "Marks inside 50 (last 5)",
  team_expected_score: "Team expected score",
  opponent_goals_conceded_avg: "Opponent goals conceded",
  team_elo_win_prob: "Elo win probability",
  tog_last5_avg: "Recent time on ground",
};

function num(v: number, digits = 1): string {
  return v.toFixed(digits);
}

function pct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

function TransparencyDrawer({ row, labels }: { row: DisposalProjection | GoalProjection; labels: Record<string, string> }) {
  const entries = Object.entries(row.input_features).filter(([k]) => k in labels);
  return (
    <tr className="projection-table__drawer">
      <td colSpan={20}>
        <div className="projection-drawer">
          {row.warnings.length > 0 && (
            <div className="projection-drawer__warnings">
              {row.warnings.map((w, i) => (
                <p key={i} className="projection-drawer__warning">
                  {w}
                </p>
              ))}
            </div>
          )}
          {row.is_stale && (
            <div className="projection-drawer__warnings">
              {row.stale_reasons.map((r, i) => (
                <p key={i} className="projection-drawer__warning projection-drawer__warning--stale">
                  Stale: {r}
                </p>
              ))}
            </div>
          )}
          <p className="projection-drawer__title">Model inputs used for this projection</p>
          <div className="projection-drawer__grid">
            {entries.map(([key, value]) => (
              <div key={key} className="projection-drawer__feature">
                <span className="projection-drawer__feature-label">{labels[key]}</span>
                <span className="projection-drawer__feature-value">{value === null ? "—" : num(value, 2)}</span>
              </div>
            ))}
          </div>
          <p className="hint">
            Model {row.model_name} · generated {formatCompactDateTime(row.generated_at)} · data as of{" "}
            {formatShortDate(row.data_cutoff)}
          </p>
        </div>
      </td>
    </tr>
  );
}

type SortKey = string;

function useSort<T>(rows: T[], defaultKey: SortKey, getValue: (row: T, key: SortKey) => number) {
  const [sortKey, setSortKey] = useState(defaultKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const sorted = [...rows].sort((a, b) => {
    const diff = getValue(a, sortKey) - getValue(b, sortKey);
    return sortDir === "asc" ? diff : -diff;
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  return { sorted, sortKey, sortDir, toggleSort };
}

function SortableHeader({ label, sortKeyName, activeKey, dir, onSort }: { label: string; sortKeyName: string; activeKey: string; dir: string; onSort: (k: string) => void }) {
  const active = activeKey === sortKeyName;
  return (
    <th className={active ? "projection-table__th projection-table__th--active" : "projection-table__th"} onClick={() => onSort(sortKeyName)}>
      {label}
      {active && <span className="projection-table__sort-arrow">{dir === "asc" ? " ↑" : " ↓"}</span>}
    </th>
  );
}

interface DisposalTableProps {
  rows: DisposalProjection[];
  thresholds?: string[];
  showMatchColumn?: boolean;
}

export function DisposalProjectionTable({ rows, thresholds = ["20", "25", "30", "35"], showMatchColumn = false }: DisposalTableProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const getValue = (row: DisposalProjection, key: string): number => {
    if (key === "expected") return row.expected;
    if (key === "player") return row.expected; // fallback, name sort handled separately
    if (thresholds.includes(key)) return row.thresholds[key]?.probability ?? 0;
    return row.expected;
  };
  const { sorted, sortKey, sortDir, toggleSort } = useSort(rows, "expected", getValue);

  if (rows.length === 0) {
    return <p className="empty-state">No disposal projections available for this selection.</p>;
  }

  return (
    <div className="projection-table-scroll">
      <table className="projection-table">
        <thead>
          <tr>
            <th className="projection-table__th">Player</th>
            {showMatchColumn && <th className="projection-table__th">Match</th>}
            <SortableHeader label="Expected" sortKeyName="expected" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
            <th className="projection-table__th">80% Range</th>
            {thresholds.map((t) => (
              <SortableHeader key={t} label={`${t}+`} sortKeyName={t} activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
            ))}
            <th className="projection-table__th">Confidence</th>
            <th className="projection-table__th">Lineup Status</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <Fragment key={`${row.match_id}-${row.player_id}`}>
              <tr
                className="projection-table__row"
                onClick={() => setExpanded(expanded === row.player_id ? null : row.player_id)}
              >
                <td>
                  <Link to={`/players/${row.player_id}`} onClick={(e) => e.stopPropagation()}>
                    {row.player_name}
                  </Link>
                  {row.is_stale && <span className="projection-table__stale-badge" title={row.stale_reasons.join(" ")}>stale</span>}
                </td>
                {showMatchColumn && <td>{row.team_name}</td>}
                <td>{num(row.expected)}</td>
                <td>
                  {row.interval_80[0]}–{row.interval_80[1]}
                </td>
                {thresholds.map((t) => (
                  <td key={t} title={row.thresholds[t]?.warning ?? undefined}>
                    {row.thresholds[t] ? pct(row.thresholds[t].probability) : "—"}
                    {row.thresholds[t]?.warning && <span className="projection-table__rare-flag">*</span>}
                  </td>
                ))}
                <td>
                  <span className={`confidence-badge confidence-badge--${row.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
                    {CONFIDENCE_LABELS[row.confidence_tier]}
                  </span>
                </td>
                <td>
                  <span className={`lineup-badge lineup-badge--${row.selection_status}`} title={row.is_stale ? "Lineup/model state has changed since this projection was generated" : undefined}>
                    {SELECTION_LABELS[row.selection_status]}
                  </span>
                </td>
              </tr>
              {expanded === row.player_id && <TransparencyDrawer key={`${row.player_id}-drawer`} row={row} labels={DISPOSAL_INPUT_LABELS} />}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="hint projection-table__footnote">* rare-event probability — smaller historical sample, treat with more caution.</p>
    </div>
  );
}

interface GoalTableProps {
  rows: GoalProjection[];
  thresholds?: string[];
  showMatchColumn?: boolean;
}

export function GoalProjectionTable({ rows, thresholds = ["1", "2", "3", "4"], showMatchColumn = false }: GoalTableProps) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const getValue = (row: GoalProjection, key: string): number => {
    if (key === "expected") return row.expected;
    if (thresholds.includes(key)) return row.thresholds[key]?.probability ?? 0;
    return row.expected;
  };
  const { sorted, sortKey, sortDir, toggleSort } = useSort(rows, "expected", getValue);

  if (rows.length === 0) {
    return <p className="empty-state">No goal projections available for this selection.</p>;
  }

  return (
    <div className="projection-table-scroll">
      <table className="projection-table">
        <thead>
          <tr>
            <th className="projection-table__th">Player</th>
            {showMatchColumn && <th className="projection-table__th">Match</th>}
            <SortableHeader label="Expected Goals" sortKeyName="expected" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
            {thresholds.map((t) => (
              <SortableHeader key={t} label={`${t}+`} sortKeyName={t} activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
            ))}
            <th className="projection-table__th">Confidence</th>
            <th className="projection-table__th">Lineup Status</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <Fragment key={`${row.match_id}-${row.player_id}`}>
              <tr
                className="projection-table__row"
                onClick={() => setExpanded(expanded === row.player_id ? null : row.player_id)}
              >
                <td>
                  <Link to={`/players/${row.player_id}`} onClick={(e) => e.stopPropagation()}>
                    {row.player_name}
                  </Link>
                  {row.is_stale && <span className="projection-table__stale-badge" title={row.stale_reasons.join(" ")}>stale</span>}
                </td>
                {showMatchColumn && <td>{row.team_name}</td>}
                <td>{num(row.expected, 2)}</td>
                {thresholds.map((t) => (
                  <td key={t} title={row.thresholds[t]?.warning ?? undefined}>
                    {row.thresholds[t] ? pct(row.thresholds[t].probability) : "—"}
                    {row.thresholds[t]?.warning && <span className="projection-table__rare-flag">*</span>}
                  </td>
                ))}
                <td>
                  <span className={`confidence-badge confidence-badge--${row.confidence_tier.replace("_confidence", "").replace("insufficient_history", "insufficient_data")}`}>
                    {CONFIDENCE_LABELS[row.confidence_tier]}
                  </span>
                </td>
                <td>
                  <span className={`lineup-badge lineup-badge--${row.selection_status}`} title={row.is_stale ? "Lineup/model state has changed since this projection was generated" : undefined}>
                    {SELECTION_LABELS[row.selection_status]}
                  </span>
                </td>
              </tr>
              {expanded === row.player_id && <TransparencyDrawer key={`${row.player_id}-drawer`} row={row} labels={GOAL_INPUT_LABELS} />}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="hint projection-table__footnote">* rare-event probability — smaller historical sample, treat with more caution.</p>
    </div>
  );
}
