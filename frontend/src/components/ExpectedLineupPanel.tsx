import { useEffect, useState } from "react";
import "./ExpectedLineupPanel.css";
import {
  bulkApplyLineup,
  bulkRemoveLineup,
  deleteMatchLineup,
  fetchLineupSummary,
  fetchMatchLineup,
  fetchPlayers,
  fetchSuggestedRoster,
  setMatchLineup,
  type AnnouncementState,
  type BulkApplyEntry,
  type ExpectedLineup,
  type ExpectedLineupStatus,
  type LineupSummary,
  type PlayerSummary,
  type RosterSuggestion,
  type SelectionStatus,
} from "../api/client";
import { formatCompactDateTime, formatShortDate } from "../lib/datetime";

const SELECTION_LABELS: Record<SelectionStatus, string> = {
  placeholder: "Placeholder",
  named_in_squad: "Named in squad",
  confirmed_selected: "Confirmed selected",
  emergency: "Emergency",
  substitute: "Substitute",
  confirmed_out: "Confirmed out",
  uncertain: "Uncertain",
};

const ANNOUNCEMENT_LABELS: Record<AnnouncementState, string> = {
  teams_not_announced: "Teams not announced",
  squad_announced: "Squad announced",
  final_team_confirmed: "Final team confirmed",
};

// A bulk-loaded placeholder roster is NOT the same claim as "nothing has
// happened yet" — derive_announcement_state (backend) correctly keeps both
// at "teams_not_announced" (a placeholder must never read as confirmed),
// but the UI headline should still tell them apart so a loaded-but-
// unreviewed roster doesn't look identical to an empty one.
function announcementHeadline(summary: LineupSummary): string {
  if (summary.announcement_state === "teams_not_announced" && summary.n_placeholder > 0) {
    return "Roster loaded — teams not confirmed";
  }
  return ANNOUNCEMENT_LABELS[summary.announcement_state];
}

// Mirrors app/models/expected_lineup.py's derive_coarse_status — kept in
// sync manually since this is display/default-picking logic only; the
// server always recomputes is_confirmed/status itself and never trusts
// this client-side mirror.
function coarseStatusFor(selectionStatus: SelectionStatus): ExpectedLineupStatus {
  if (selectionStatus === "confirmed_selected" || selectionStatus === "substitute") return "expected_in";
  if (selectionStatus === "confirmed_out") return "expected_out";
  return "uncertain";
}

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
  const [summary, setSummary] = useState<LineupSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const [searchResults, setSearchResults] = useState<PlayerSummary[]>([]);
  const [search, setSearch] = useState("");
  const [addTeamId, setAddTeamId] = useState(homeTeamId);
  const [addSelectionStatus, setAddSelectionStatus] = useState<SelectionStatus>("confirmed_selected");
  const [addPlayerId, setAddPlayerId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [suggestTeamId, setSuggestTeamId] = useState(homeTeamId);
  const [suggestions, setSuggestions] = useState<RosterSuggestion[]>([]);
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState<Set<number>>(new Set());
  const [bulkStatus, setBulkStatus] = useState<SelectionStatus>("placeholder");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkReportMsg, setBulkReportMsg] = useState<string | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [lineupData, summaryData] = await Promise.all([fetchMatchLineup(matchId), fetchLineupSummary(matchId)]);
      setLineup(lineupData);
      setSummary(summaryData);
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
      await setMatchLineup(matchId, addPlayerId, {
        player_id: addPlayerId,
        team_id: addTeamId,
        status: coarseStatusFor(addSelectionStatus),
        selection_status: addSelectionStatus,
      });
      setSearch("");
      setSearchResults([]);
      setAddPlayerId(null);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set lineup status");
    }
  }

  async function handleStatusChange(entry: ExpectedLineup, selectionStatus: SelectionStatus) {
    await setMatchLineup(matchId, entry.player_id, {
      player_id: entry.player_id,
      team_id: entry.team_id,
      status: coarseStatusFor(selectionStatus),
      selection_status: selectionStatus,
    });
    await load();
    onChanged?.();
  }

  async function handleRemove(entry: ExpectedLineup) {
    await deleteMatchLineup(matchId, entry.player_id);
    await load();
    onChanged?.();
  }

  async function loadSuggestions() {
    setBulkReportMsg(null);
    setSuggestions(await fetchSuggestedRoster(matchId, suggestTeamId));
    setSelectedSuggestionIds(new Set());
  }

  function toggleSuggestion(playerId: number) {
    setSelectedSuggestionIds((prev) => {
      const next = new Set(prev);
      if (next.has(playerId)) next.delete(playerId);
      else next.add(playerId);
      return next;
    });
  }

  async function applyBulk() {
    if (selectedSuggestionIds.size === 0) return;
    setBulkBusy(true);
    setBulkReportMsg(null);
    try {
      const entries: BulkApplyEntry[] = suggestions
        .filter((s) => selectedSuggestionIds.has(s.player_id))
        .map((s) => ({ player_id: s.player_id, team_id: suggestTeamId, selection_status: bulkStatus }));
      const result = await bulkApplyLineup(matchId, entries);
      const parts = [`${result.created.length} created`, `${result.updated.length} updated`];
      if (result.skipped_manual_override.length) parts.push(`${result.skipped_manual_override.length} skipped (manual override)`);
      if (result.unresolved.length) parts.push(`${result.unresolved.length} unresolved`);
      setBulkReportMsg(`Applied: ${parts.join(", ")}.`);
      await load();
      onChanged?.();
    } catch (err) {
      setBulkReportMsg(err instanceof Error ? err.message : "Bulk apply failed");
    } finally {
      setBulkBusy(false);
    }
  }

  const homeLineup = lineup.filter((l) => l.team_id === homeTeamId);
  const awayLineup = lineup.filter((l) => l.team_id === awayTeamId);

  // Section 7 (live-operations stage): players currently on this match's
  // lineup for the team just loaded, but NOT present in the freshly-loaded
  // suggested roster - i.e. players who look to have been omitted/dropped
  // since that reference roster was set. Only meaningful once a suggestion
  // has actually been loaded for a team.
  const suggestedPlayerIds = new Set(suggestions.map((s) => s.player_id));
  const omittedFromSuggestion =
    suggestions.length > 0 ? lineup.filter((l) => l.team_id === suggestTeamId && !suggestedPlayerIds.has(l.player_id)) : [];

  async function removeOmitted() {
    if (omittedFromSuggestion.length === 0) return;
    setRemoveBusy(true);
    setBulkReportMsg(null);
    try {
      const result = await bulkRemoveLineup(matchId, omittedFromSuggestion.map((l) => l.player_id));
      setBulkReportMsg(`Removed ${result.removed.length} player(s) not in the new roster.`);
      await load();
      onChanged?.();
    } catch (err) {
      setBulkReportMsg(err instanceof Error ? err.message : "Bulk remove failed");
    } finally {
      setRemoveBusy(false);
    }
  }

  function renderTeamList(teamLineup: ExpectedLineup[], teamName: string) {
    return (
      <div className="lineup-panel__team">
        <h4>{teamName}</h4>
        {teamLineup.length === 0 && <p className="empty-state">No expected-lineup entries yet.</p>}
        {teamLineup.map((entry) => (
          <div key={entry.id} className={`lineup-panel__entry lineup-panel__entry--${entry.selection_status}`}>
            <span className="lineup-panel__entry-name">
              {entry.player_name}
              {entry.is_manual_override && (
                <span className="lineup-panel__override-badge" title="Manually overridden — bulk/automated refreshes will not change this">
                  manual
                </span>
              )}
            </span>
            <select value={entry.selection_status} onChange={(e) => handleStatusChange(entry, e.target.value as SelectionStatus)}>
              {Object.entries(SELECTION_LABELS).map(([value, label]) => (
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
        No automated team-selection data is used — mark players manually below (see the app's team-selection source
        audit: no reliable, stable automated source currently exists). Confirmed-out players are never projected.
      </p>

      {summary && (
        <div className={`lineup-panel__announcement lineup-panel__announcement--${summary.announcement_state}`}>
          <strong>{announcementHeadline(summary)}</strong>
          {" — "}
          {summary.n_confirmed_selected} confirmed, {summary.n_named_in_squad} named in squad,{" "}
          {summary.n_uncertain + summary.n_placeholder} uncertain/placeholder, {summary.n_confirmed_out} confirmed out
          {summary.n_manual_overrides > 0 && `, ${summary.n_manual_overrides} manual override(s)`}
          {summary.last_updated && <span className="hint"> · last updated {formatCompactDateTime(summary.last_updated)}</span>}
        </div>
      )}

      {loading ? (
        <p className="loading-state">Loading…</p>
      ) : (
        <div className="lineup-panel__teams">
          {renderTeamList(homeLineup, homeTeamName)}
          {renderTeamList(awayLineup, awayTeamName)}
        </div>
      )}

      <div className="lineup-panel__bulk">
        <h4>Bulk workflow</h4>
        <p className="hint">
          Load a team's most recent roster as a starting point, review it, then apply a status to the selected
          players in one call — practical for updating all 18 teams. Nothing here is applied until you click Apply.
        </p>
        <div className="lineup-panel__bulk-controls">
          <label>
            Team
            <select
              value={suggestTeamId}
              onChange={(e) => {
                setSuggestTeamId(Number(e.target.value));
                setSuggestions([]);
                setSelectedSuggestionIds(new Set());
              }}
            >
              <option value={homeTeamId}>{homeTeamName}</option>
              <option value={awayTeamId}>{awayTeamName}</option>
            </select>
          </label>
          <button type="button" onClick={loadSuggestions}>
            Load suggested roster
          </button>
        </div>

        {suggestions.length > 0 && (
          <>
            <div className="lineup-panel__bulk-list">
              <div className="lineup-panel__bulk-list-header">
                <button type="button" onClick={() => setSelectedSuggestionIds(new Set(suggestions.map((s) => s.player_id)))}>
                  Select all
                </button>
                <button type="button" onClick={() => setSelectedSuggestionIds(new Set())}>
                  Clear
                </button>
                <span className="hint">
                  {selectedSuggestionIds.size} of {suggestions.length} selected
                </span>
              </div>
              {suggestions.map((s) => (
                <label key={s.player_id} className="lineup-panel__bulk-item">
                  <input type="checkbox" checked={selectedSuggestionIds.has(s.player_id)} onChange={() => toggleSuggestion(s.player_id)} />
                  {s.display_name}
                  <span className="hint">last played {formatShortDate(s.last_played_at)}</span>
                </label>
              ))}
            </div>
            <div className="lineup-panel__bulk-apply">
              <label>
                Apply status
                <select value={bulkStatus} onChange={(e) => setBulkStatus(e.target.value as SelectionStatus)}>
                  {Object.entries(SELECTION_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" disabled={bulkBusy || selectedSuggestionIds.size === 0} onClick={applyBulk}>
                {bulkBusy ? "Applying…" : `Apply to ${selectedSuggestionIds.size} player(s)`}
              </button>
            </div>
            {omittedFromSuggestion.length > 0 && (
              <div className="lineup-panel__diff">
                <p className="hint">
                  <strong>{omittedFromSuggestion.length} player(s)</strong> currently on this match's lineup for this
                  team are NOT in the roster you just loaded — likely dropped/omitted:
                </p>
                <ul className="lineup-panel__diff-list">
                  {omittedFromSuggestion.map((l) => (
                    <li key={l.player_id}>{l.player_name}</li>
                  ))}
                </ul>
                <button type="button" disabled={removeBusy} onClick={removeOmitted}>
                  {removeBusy ? "Removing…" : `Remove all ${omittedFromSuggestion.length}`}
                </button>
              </div>
            )}
            {bulkReportMsg && <p className="hint">{bulkReportMsg}</p>}
          </>
        )}
      </div>

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
          <select value={addSelectionStatus} onChange={(e) => setAddSelectionStatus(e.target.value as SelectionStatus)}>
            {Object.entries(SELECTION_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Set status (manual override)</button>
      </form>
      {error && <p className="lineup-panel__error">{error}</p>}
    </section>
  );
}

export default ExpectedLineupPanel;
