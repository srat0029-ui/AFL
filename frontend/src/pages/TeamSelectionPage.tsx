import { useEffect, useState } from "react";
import {
  bulkApplyLineup,
  fetchLineupSummary,
  fetchMatchLineup,
  fetchMatches,
  fetchSuggestedRoster,
  type BulkApplyEntry,
  type LineupSummary,
  type MatchSummary,
  type RosterSuggestion,
} from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";
import "./TeamSelectionPage.css";

interface PlayerRow {
  playerId: number;
  name: string;
  checked: boolean; // ticked = confirmed_selected
  originallyConfirmed: boolean;
}

interface TeamPanelState {
  players: PlayerRow[] | null;
  loading: boolean;
  saving: boolean;
  saved: boolean;
  error: string | null;
}

function emptyTeamPanel(): TeamPanelState {
  return { players: null, loading: false, saving: false, saved: false, error: null };
}

async function loadTeamRoster(matchId: number, teamId: number): Promise<PlayerRow[]> {
  const [lineup, suggestions] = await Promise.all([
    fetchMatchLineup(matchId),
    fetchSuggestedRoster(matchId, teamId).catch(() => [] as RosterSuggestion[]),
  ]);
  const existingForTeam = lineup.filter((l) => l.team_id === teamId);
  const byPlayerId = new Map<number, PlayerRow>();
  for (const l of existingForTeam) {
    byPlayerId.set(l.player_id, { playerId: l.player_id, name: l.player_name, checked: l.selection_status === "confirmed_selected", originallyConfirmed: l.selection_status === "confirmed_selected" });
  }
  for (const s of suggestions) {
    if (!byPlayerId.has(s.player_id)) {
      byPlayerId.set(s.player_id, { playerId: s.player_id, name: s.display_name, checked: false, originallyConfirmed: false });
    }
  }
  return [...byPlayerId.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function TeamChecklist({ matchId, teamId, teamName, onSaved }: { matchId: number; teamId: number; teamName: string; onSaved: () => void }) {
  const [state, setState] = useState<TeamPanelState>(emptyTeamPanel());

  useEffect(() => {
    setState({ ...emptyTeamPanel(), loading: true });
    loadTeamRoster(matchId, teamId)
      .then((players) => setState({ players, loading: false, saving: false, saved: false, error: null }))
      .catch((err) => setState({ players: null, loading: false, saving: false, saved: false, error: err instanceof Error ? err.message : "Failed to load roster" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId, teamId]);

  function toggle(playerId: number) {
    setState((s) => (s.players ? { ...s, players: s.players.map((p) => (p.playerId === playerId ? { ...p, checked: !p.checked } : p)), saved: false } : s));
  }

  function setAll(checked: boolean) {
    setState((s) => (s.players ? { ...s, players: s.players.map((p) => ({ ...p, checked })), saved: false } : s));
  }

  async function handleSave() {
    if (!state.players) return;
    setState((s) => ({ ...s, saving: true, error: null }));
    const entries: BulkApplyEntry[] = [];
    for (const p of state.players) {
      if (p.checked) {
        entries.push({ player_id: p.playerId, team_id: teamId, selection_status: "confirmed_selected" });
      } else if (p.originallyConfirmed) {
        // was confirmed, now unticked - revert rather than leave a stale "confirmed" row.
        entries.push({ player_id: p.playerId, team_id: teamId, selection_status: "uncertain" });
      }
    }
    try {
      if (entries.length > 0) {
        await bulkApplyLineup(matchId, entries, { source: "manual_tick_list" });
      }
      const refreshed = await loadTeamRoster(matchId, teamId);
      setState({ players: refreshed, loading: false, saving: false, saved: true, error: null });
      onSaved();
    } catch (err) {
      setState((s) => ({ ...s, saving: false, error: err instanceof Error ? err.message : "Failed to save" }));
    }
  }

  const nChecked = state.players?.filter((p) => p.checked).length ?? 0;

  return (
    <div className="team-selection__team">
      <div className="team-selection__team-header">
        <h3>{teamName}</h3>
        <span className="hint">{nChecked} ticked</span>
      </div>

      {state.loading && <p className="loading-state">Loading roster…</p>}
      {state.error && <div className="error-banner">{state.error}</div>}

      {state.players && (
        <>
          <div className="team-selection__quick-actions">
            <button type="button" className="btn" onClick={() => setAll(true)}>
              Select all
            </button>
            <button type="button" className="btn" onClick={() => setAll(false)}>
              Clear
            </button>
          </div>

          {state.players.length === 0 && <p className="empty-state">No roster candidates found for this team yet.</p>}

          <ul className="team-selection__list">
            {state.players.map((p) => (
              <li key={p.playerId}>
                <label>
                  <input type="checkbox" checked={p.checked} onChange={() => toggle(p.playerId)} />
                  {p.name}
                </label>
              </li>
            ))}
          </ul>

          <button type="button" className="btn team-selection__save" onClick={handleSave} disabled={state.saving}>
            {state.saving ? "Saving…" : "Save"}
          </button>
          {state.saved && <span className="team-selection__saved-flag">Saved</span>}
        </>
      )}
    </div>
  );
}

function MatchAccordionRow({ match, expanded, onToggle }: { match: MatchSummary; expanded: boolean; onToggle: () => void }) {
  const [summary, setSummary] = useState<LineupSummary | null>(null);

  function loadSummary() {
    fetchLineupSummary(match.id).then(setSummary).catch(() => setSummary(null));
  }

  useEffect(loadSummary, [match.id]);

  return (
    <div className="team-selection__match">
      <button type="button" className="team-selection__match-toggle" onClick={onToggle}>
        <span className="team-selection__match-teams">
          {match.home_team.name} v {match.away_team.name}
        </span>
        <span className="hint">{formatCompactDateTime(match.scheduled_start)}</span>
        {summary && (
          <span className="hint">
            {summary.n_confirmed_selected} confirmed · {summary.n_placeholder + summary.n_uncertain} unconfirmed
          </span>
        )}
        <span className="team-selection__chevron">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div className="team-selection__teams">
          <TeamChecklist matchId={match.id} teamId={match.home_team.id} teamName={match.home_team.name} onSaved={loadSummary} />
          <TeamChecklist matchId={match.id} teamId={match.away_team.id} teamName={match.away_team.name} onSaved={loadSummary} />
        </div>
      )}
    </div>
  );
}

/** A fast round-wide checklist for marking who's actually playing — the
 * manual counterpart to the per-match Expected Lineup panel, since there's
 * no automated AFL team-selection feed to auto-populate this from (see
 * team_selection_ingestion.py's module docstring). Ticking a player marks
 * them confirmed_selected via the SAME bulk-apply endpoint the per-match
 * panel already uses; nothing new on the backend. */
function TeamSelectionPage() {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedMatchId, setExpandedMatchId] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchMatches("scheduled")
      .then((all) => {
        if (all.length === 0) {
          setMatches([]);
          return;
        }
        const sorted = [...all].sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start));
        const nextRound = sorted[0].round_number;
        setMatches(sorted.filter((m) => m.round_number === nextRound));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load matches"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="team-selection-page">
      <header className="team-selection-page__header">
        <h1>Team Selection</h1>
        <p className="hint">
          Tick who's actually playing for each team this round. No automated AFL team-selection feed exists, so this
          is a fast manual checklist — ticking a player marks them confirmed, which unlocks Best Opportunities,
          Final Shortlist, and Multi Builder's confirmed-player views for that match.
        </p>
      </header>

      {loading && <p className="loading-state">Loading…</p>}
      {error && <div className="error-banner">{error}</div>}
      {!loading && !error && matches.length === 0 && <p className="empty-state">No upcoming matches found.</p>}

      <div className="team-selection-page__matches">
        {matches.map((m) => (
          <MatchAccordionRow key={m.id} match={m} expanded={expandedMatchId === m.id} onToggle={() => setExpandedMatchId((cur) => (cur === m.id ? null : m.id))} />
        ))}
      </div>
    </main>
  );
}

export default TeamSelectionPage;
