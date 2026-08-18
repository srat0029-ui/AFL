import { useState } from "react";
import { Link } from "react-router-dom";
import type { PlayerGameStat } from "../api/client";
import { formatShortDateWithYear } from "../lib/datetime";

export interface Column {
  key: keyof PlayerGameStat | "opponent" | "round";
  label: string;
}

const DEFAULT_COLUMNS: Column[] = [
  { key: "round", label: "Round" },
  { key: "opponent", label: "Opponent" },
  { key: "disposals", label: "DI" },
  { key: "kicks", label: "KI" },
  { key: "handballs", label: "HB" },
  { key: "marks", label: "MK" },
  { key: "tackles", label: "TK" },
  { key: "goals", label: "GL" },
  { key: "behinds", label: "BH" },
];

function num(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

const formatDate = formatShortDateWithYear;

/** Sortable player-match-stats table. showPlayerColumn=true (match-detail
 * pages, many players) adds a leading player-name column linking to their
 * profile; false (a single player's own game log) omits it since it's
 * redundant there. */
function PlayerStatsTable({
  games,
  showPlayerColumn = false,
  columns = DEFAULT_COLUMNS,
}: {
  games: PlayerGameStat[];
  showPlayerColumn?: boolean;
  columns?: Column[];
}) {
  const [sortKey, setSortKey] = useState<Column["key"] | "player_display_name">(showPlayerColumn ? "disposals" : "round");
  const [sortDesc, setSortDesc] = useState(true);

  function handleSort(key: Column["key"] | "player_display_name") {
    if (key === sortKey) {
      setSortDesc((d) => !d);
    } else {
      setSortKey(key);
      setSortDesc(true);
    }
  }

  function sortValue(g: PlayerGameStat, key: Column["key"] | "player_display_name"): number | string {
    if (key === "opponent") return g.opponent_team?.name ?? "";
    if (key === "round") return g.round_number;
    if (key === "player_display_name") return g.player_display_name;
    const v = g[key as keyof PlayerGameStat];
    return typeof v === "number" ? v : v === null || v === undefined ? -Infinity : String(v);
  }

  const sorted = [...games].sort((a, b) => {
    const av = sortValue(a, sortKey);
    const bv = sortValue(b, sortKey);
    const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
    return sortDesc ? -cmp : cmp;
  });

  return (
    <div className="segment-table-scroll">
      <table className="segment-table">
        <thead>
          <tr>
            {showPlayerColumn && (
              <th className="player-stats-table__sortable" onClick={() => handleSort("player_display_name")}>
                Player{sortKey === "player_display_name" ? (sortDesc ? " ↓" : " ↑") : ""}
              </th>
            )}
            {columns.map((c) => (
              <th key={c.key} className="player-stats-table__sortable" onClick={() => handleSort(c.key)}>
                {c.label}
                {sortKey === c.key ? (sortDesc ? " ↓" : " ↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((g) => (
            <tr key={`${g.player_id}-${g.match_id}`}>
              {showPlayerColumn && (
                <td>
                  <Link to={`/players/${g.player_id}`}>{g.player_display_name}</Link>
                  {(g.subbed_on || g.subbed_off) && (
                    <span className="player-stats-table__sub-flag"> {g.subbed_on ? "↑" : "↓"}</span>
                  )}
                </td>
              )}
              {columns.map((c) => {
                if (c.key === "round") {
                  return (
                    <td key={c.key}>
                      R{g.round_number} · {formatDate(g.scheduled_start)}
                    </td>
                  );
                }
                if (c.key === "opponent") {
                  return <td key={c.key}>{g.opponent_team?.short_name ?? "—"}</td>;
                }
                const value = g[c.key as keyof PlayerGameStat];
                return <td key={c.key}>{typeof value === "number" ? num(value) : "—"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {games.length === 0 && <p className="hint">No games recorded.</p>}
    </div>
  );
}

export default PlayerStatsTable;
