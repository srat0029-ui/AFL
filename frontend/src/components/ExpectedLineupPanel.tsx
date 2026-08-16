import { useEffect, useState } from "react";
import "./ExpectedLineupPanel.css";
import {
  deleteMatchLineup,
  fetchMatchLineup,
  fetchPlayers,
  setMatchLineup,
  type ExpectedLineup,
  type ExpectedLineupStatus,
  type PlayerSummary,
} from "../api/client";

const STATUS_LABELS: Record<ExpectedLineupStatus, string> = {
  expected_in: "Expected in",
  uncertain: "Uncertain",
  expected_out: "Expected out",
};

interface ExpectedLineupPanelProps {
  matchId: number;
  homeTeamId: number;
  awayTeamId: number;
  homeTeamName: string;
  awayTeamName: string;
  onChanged?: () => void;
}

function ExpectedLineupPanel({ matchId, homeTeamId, awayTeamId, homeTeamName, awayTeamName, onChanged }: ExpectedLineupPanelProps) {
  const [lineup, setLineup] = useState<ExpectedLineup[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchResults, setSearchResults] = useState<PlayerSummary[]>([]);
  const [search, setSearch] = useState("");
  const [addTeamId, setAddTeamId] = useState(homeTeamId);
  const [addStatus, setAddStatus] = useState<ExpectedLineupStatus>("expected_in");
  const [addPlayerId, setAddPlayerId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setLineup(await fetchMatchLineup(matchId));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  useEffect(() => {
    if (search.trim().length < 2) {
      setSearchResults([]);
      return;
    }
    const handle = setTimeout(() => {
      fetchPlayers({ teamId: addTeamId, name: search, limit: 10 })
        .then((res) => setSearchResults(res.players))
        .catch(() => setSearchResults([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [search, addTeamId]);

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (addPlayerId === null) {
      setError("Select a player from the search results first.");
      return;
    }
    try {
      await setMatchLineup(matchId, addPlayerId, { player_id: addPlayerId, team_id: addTeamId, status: addStatus });
      setSearch("");
      setSearchResults([]);
      setAddPlayerId(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set lineup status");
    }
  }

  async function handleStatusChange(entry: ExpectedLineup, status: ExpectedLineupStatus) {
    await setMatchLineup(matchId, entry.player_id, { player_id: entry.player_id, team_id: entry.team_id, status });
    await load();
    onChanged?.();
  }

  async function handleRemove(entry: ExpectedLineup) {
    await deleteMatchLineup(matchId, entry.player_id);
    await load();
    onChanged?.();
  }

  const homeLineup = lineup.filter((l) => l.team_id === homeTeamId);
  const awayLineup = lineup.filter((l) => l.team_id === awayTeamId);

  function renderTeamList(teamLineup: ExpectedLineup[], teamName: string) {
    return (
      <div className="lineup-panel__team">
        <h4>{teamName}</h4>
        {teamLineup.length === 0 && <p className="hint">No expected-lineup entries yet.</p>}
        {teamLineup.map((entry) => (
          <div key={entry.id} className="lineup-panel__entry">
            <span className="lineup-panel__entry-name">{entry.player_name}</span>
            <select value={entry.status} onChange={(e) => handleStatusChange(entry, e.target.value as ExpectedLineupStatus)}>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            {entry.substitute_risk && <span className="lineup-panel__tag">sub risk</span>}
            {entry.returning_from_injury && <span className="lineup-panel__tag">returning</span>}
            <button type="button" className="lineup-panel__remove" onClick={() => handleRemove(entry)}>
              Remove
            </button>
          </div>
        ))}
      </div>
    );
  }

  return (
    <section className="lineup-panel">
      <h3>Expected lineup</h3>
      <p className="hint">
        No automated team-selection data is used — mark players manually below. Projections are only generated for
        players marked "Expected in" or "Uncertain."
      </p>

      {loading ? (
        <p className="hint">Loading…</p>
      ) : (
        <div className="lineup-panel__teams">
          {renderTeamList(homeLineup, homeTeamName)}
          {renderTeamList(awayLineup, awayTeamName)}
        </div>
      )}

      <form className="lineup-panel__add-form" onSubmit={handleAdd}>
        <label>
          Team
          <select value={addTeamId} onChange={(e) => setAddTeamId(Number(e.target.value))}>
            <option value={homeTeamId}>{homeTeamName}</option>
            <option value={awayTeamId}>{awayTeamName}</option>
          </select>
        </label>
        <label className="lineup-panel__search">
          Player
          <input
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setAddPlayerId(null);
            }}
            placeholder="Search player name…"
          />
          {searchResults.length > 0 && (
            <ul className="lineup-panel__search-results">
              {searchResults.map((p) => (
                <li key={p.id} onClick={() => { setAddPlayerId(p.id); setSearch(p.display_name); setSearchResults([]); }}>
                  {p.display_name}
                </li>
              ))}
            </ul>
          )}
        </label>
        <label>
          Status
          <select value={addStatus} onChange={(e) => setAddStatus(e.target.value as ExpectedLineupStatus)}>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Set status</button>
      </form>
      {error && <p className="lineup-panel__error">{error}</p>}
    </section>
  );
}

export default ExpectedLineupPanel;
