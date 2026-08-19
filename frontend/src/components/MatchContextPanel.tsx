import { useEffect, useState } from "react";
import "./MatchContextPanel.css";
import {
  createMatchContextItem,
  fetchMatchContextPanel,
  fetchPlayers,
  refreshWeather,
  type ContextConfidence,
  type ContextType,
  type MatchContextPanel as MatchContextPanelData,
  type PlayerSummary,
} from "../api/client";
import { formatCompactDateTime } from "../lib/datetime";

const CONTEXT_TYPE_OPTIONS: { value: ContextType; label: string }[] = [
  { value: "confirmed_in", label: "Confirmed in" },
  { value: "confirmed_out", label: "Confirmed out" },
  { value: "injury", label: "Injury" },
  { value: "late_withdrawal", label: "Late withdrawal" },
  { value: "named_substitute", label: "Named substitute" },
  { value: "emergency", label: "Emergency" },
  { value: "returning_player", label: "Returning player" },
  { value: "limited_game_time_concern", label: "Limited game-time concern" },
  { value: "weather", label: "Weather" },
  { value: "venue_condition", label: "Venue condition" },
  { value: "major_role_change", label: "Major role-change note" },
  { value: "other", label: "Other verified team news" },
];

const CONFIDENCE_OPTIONS: { value: ContextConfidence; label: string }[] = [
  { value: "official", label: "Official announcement" },
  { value: "reputable_source", label: "Reputable source" },
  { value: "unverified", label: "Unverified" },
];

const LINEUP_APPLYABLE_TYPES = new Set<ContextType>(["confirmed_in", "confirmed_out", "named_substitute", "emergency", "late_withdrawal"]);
const NON_PLAYER_TYPES = new Set<ContextType>(["weather", "venue_condition", "other"]);
const FRESHNESS_LABELS: Record<string, string> = { fresh: "Fresh", aging: "Aging", stale: "Stale" };

interface MatchContextPanelProps {
  matchId: number;
  homeTeamId: number;
  awayTeamId: number;
  homeTeamName: string;
  awayTeamName: string;
}

function MatchContextPanel({ matchId, homeTeamId, awayTeamId, homeTeamName, awayTeamName }: MatchContextPanelProps) {
  const [panel, setPanel] = useState<MatchContextPanelData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [weatherBusy, setWeatherBusy] = useState(false);

  const [contextType, setContextType] = useState<ContextType>("confirmed_out");
  const [teamId, setTeamId] = useState(homeTeamId);
  const [source, setSource] = useState("");
  const [confidence, setConfidence] = useState<ContextConfidence>("unverified");
  const [summary, setSummary] = useState("");
  const [sourceTimestamp, setSourceTimestamp] = useState("");
  const [applyToLineup, setApplyToLineup] = useState(false);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<PlayerSummary[]>([]);
  const [playerId, setPlayerId] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    try {
      setPanel(await fetchMatchContextPanel(matchId));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  useEffect(() => {
    if (search.trim().length < 2 || playerId !== null) {
      setSearchResults([]);
      return;
    }
    const handle = setTimeout(() => {
      fetchPlayers({ teamId, name: search, limit: 10 })
        .then((res) => setSearchResults(res.players))
        .catch(() => setSearchResults([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [search, teamId, playerId]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setInfo(null);
    if (!source.trim() || !summary.trim()) {
      setError("Source and summary are required.");
      return;
    }
    setBusy(true);
    try {
      const result = await createMatchContextItem(matchId, {
        context_type: contextType,
        source: source.trim(),
        summary: summary.trim(),
        confidence,
        team_id: teamId,
        player_id: playerId,
        source_timestamp: sourceTimestamp ? new Date(sourceTimestamp).toISOString() : null,
        apply_to_lineup: applyToLineup,
      });
      setSummary("");
      setSource("");
      setSearch("");
      setSearchResults([]);
      setPlayerId(null);
      setApplyToLineup(false);
      if (result.lineup_apply_note) setInfo(result.lineup_apply_note);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add context item");
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshWeather() {
    setWeatherBusy(true);
    try {
      await refreshWeather();
      await load();
    } catch {
      // best-effort - no dedicated error UI needed for this compact panel
    } finally {
      setWeatherBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="context-panel">
        <p className="loading-state">Loading current context…</p>
      </section>
    );
  }
  if (!panel) return null;

  const isPlayerType = !NON_PLAYER_TYPES.has(contextType);
  const canApplyToLineup = LINEUP_APPLYABLE_TYPES.has(contextType) && playerId !== null;

  return (
    <section className="context-panel">
      <div className="context-panel__header">
        <h2>Current Context</h2>
        <button type="button" className="context-panel__toggle" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Close" : "+ Add context"}
        </button>
      </div>

      {panel.last_updated && <p className="hint">Last updated {formatCompactDateTime(panel.last_updated)}</p>}

      {panel.weather ? (
        <div className="context-panel__weather">
          <span>{panel.weather.temperature_c?.toFixed(0)}°C</span>
          <span>
            Rain {panel.weather.rain_probability_pct?.toFixed(0)}%
            {panel.weather.expected_rainfall_mm ? ` (${panel.weather.expected_rainfall_mm.toFixed(1)}mm)` : ""}
          </span>
          <span>
            Wind {panel.weather.wind_speed_kph?.toFixed(0)} km/h, gusts {panel.weather.wind_gust_kph?.toFixed(0)} km/h
          </span>
          {panel.weather.severe_weather_warning && <span className="context-panel__severe">⚠ {panel.weather.severe_weather_note}</span>}
        </div>
      ) : (
        <div className="context-panel__weather context-panel__weather--empty">
          <span className="hint">No weather forecast recorded yet.</span>
          <button type="button" onClick={handleRefreshWeather} disabled={weatherBusy} className="context-panel__refresh">
            {weatherBusy ? "Fetching…" : "Fetch forecast"}
          </button>
        </div>
      )}

      {panel.current_context.length === 0 ? (
        <p className="empty-state">No team news recorded for this match yet.</p>
      ) : (
        <ul className="context-panel__list">
          {panel.current_context.map((item) => (
            <li key={item.id} className={`context-panel__item context-panel__item--${item.freshness}`}>
              <span className="context-panel__type">{item.context_type_label}</span>
              {item.player_name && <span className="context-panel__player">{item.player_name}</span>}
              <span className="context-panel__summary">{item.summary}</span>
              <span className="context-panel__meta">
                {item.source} · {item.confidence_label} · {FRESHNESS_LABELS[item.freshness]}
              </span>
            </li>
          ))}
        </ul>
      )}

      {showForm && (
        <form className="context-panel__form" onSubmit={handleSubmit}>
          <div className="context-panel__form-row">
            <label>
              Type
              <select value={contextType} onChange={(e) => setContextType(e.target.value as ContextType)}>
                {CONTEXT_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Team
              <select value={teamId} onChange={(e) => setTeamId(Number(e.target.value))}>
                <option value={homeTeamId}>{homeTeamName}</option>
                <option value={awayTeamId}>{awayTeamName}</option>
              </select>
            </label>
            <label>
              Confidence
              <select value={confidence} onChange={(e) => setConfidence(e.target.value as ContextConfidence)}>
                {CONFIDENCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {isPlayerType && (
            <label className="context-panel__search">
              Player (optional)
              <input
                type="text"
                value={search}
                placeholder="Search player name…"
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPlayerId(null);
                }}
              />
              {searchResults.length > 0 && (
                <ul className="context-panel__search-results">
                  {searchResults.map((p) => (
                    <li key={p.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setPlayerId(p.id);
                          setSearch(p.display_name);
                          setSearchResults([]);
                        }}
                      >
                        {p.display_name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </label>
          )}

          <label className="context-panel__source">
            Source
            <input
              type="text"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder='e.g. "Official team announcement", "AFL.com.au", "Manual"'
            />
          </label>

          <label className="context-panel__summary-input">
            Summary
            <textarea value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={500} rows={2} placeholder="Short factual summary" />
          </label>

          <label className="context-panel__timestamp">
            Source timestamp (optional)
            <input type="datetime-local" value={sourceTimestamp} onChange={(e) => setSourceTimestamp(e.target.value)} />
          </label>

          {canApplyToLineup && (
            <label className="context-panel__checkbox">
              <input type="checkbox" checked={applyToLineup} onChange={(e) => setApplyToLineup(e.target.checked)} />
              Also update the Expected Lineup for this player
            </label>
          )}

          {error && <p className="context-panel__error">{error}</p>}
          {info && <p className="context-panel__info">{info}</p>}

          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Add context item"}
          </button>
        </form>
      )}
    </section>
  );
}

export default MatchContextPanel;
