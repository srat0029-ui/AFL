import { useEffect, useState } from "react";
import PlayerContextEvidence from "../components/PlayerContextEvidence";
import Disclaimer from "../components/Disclaimer";
import { ApiError, fetchPlayerContext, fetchPlayers, type PlayerSummary } from "../api/client";
import { explainDifference, formatDifference, formatRate, formatValue, type ContextSplit, type ContextStat, type PlayerContextResearch } from "../features/playerContext";
import "./PlayerResearchPage.css";

function PlayerPicker({ label, value, onChange, excludeId }: {
  label: string; value: PlayerSummary | null; onChange: (player: PlayerSummary | null) => void; excludeId?: number;
}) {
  const [query, setQuery] = useState("");
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ loading: boolean; error: string | null; players: PlayerSummary[]; total: number }>({ loading: false, error: null, players: [], total: 0 });
  useEffect(() => {
    let active = true;
    if (query.trim().length < 2 || value) return;
    const timer = window.setTimeout(() => {
      fetchPlayers({ name: query.trim(), limit: 50 }).then(result => {
        if (active) setState({ loading: false, error: null, players: result.players.filter(p => p.id !== excludeId), total: result.total });
      }).catch(error => {
        if (active) setState({ loading: false, error: error instanceof Error ? error.message : "Could not search players.", players: [], total: 0 });
      });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [query, value, excludeId, retry]);
  return <div className="research-picker">
    <label className="research-select-field">{label}
      <input type="search" value={value ? value.display_name : query} placeholder="Search by name" onChange={event => {
        const next = event.target.value;
        onChange(null); setQuery(next); setState({ loading: next.trim().length >= 2, error: null, players: [], total: 0 });
      }} />
    </label>
    {value ? <div className="research-selected-player"><p className="hint">Selected: {value.display_name} · {value.current_team?.name ?? "Team unavailable"}</p><button type="button" onClick={() => { onChange(null); setQuery(""); setState({ loading: false, error: null, players: [], total: 0 }); }}>Change {label.toLowerCase()}</button></div> : query.trim().length < 2 ? <p className="hint">Enter at least two characters.</p> : <div aria-live="polite">
      {state.loading && <p role="status">Searching players…</p>}
      {state.error && <div role="alert"><p>{state.error}</p><button type="button" onClick={() => { setState({ ...state, loading: true, error: null }); setRetry(retry + 1); }}>Retry search</button></div>}
      {!state.loading && !state.error && <>
        {state.players.length === 0 && <p>No matching players found.</p>}
        <ul className="research-search-results" aria-label={`${label} search results`}>{state.players.map(player => <li key={player.id}><button type="button" onClick={() => { onChange(player); setQuery(""); }}>{player.display_name}<small>{player.current_team?.name ?? "Team unavailable"} · ID {player.id}</small></button></li>)}</ul>
        {state.total > 50 && <p className="hint">Showing up to 50 matches. Refine the name to find your player.</p>}
      </>}
    </div>}
  </div>;
}

function SplitCard({ title, split, stat }: { title: string; split: ContextSplit; stat: string }) {
  return <article className="research-split-card">
    <div className="research-split-card__heading"><h3>{title}</h3><span className="chip chip--neutral">{split.games} games</span></div>
    <strong className={`research-split-card__average num ${split.mean == null ? "research-value-unavailable" : ""}`}>{formatValue(split.mean)}</strong>
    <span className="research-split-card__average-label">average {stat}</span>
    <p className="hint">{split.stat_sample_size} games with recorded {stat}; missing values are excluded.</p>
    <dl className="research-mini-stats">
      <div><dt>Median</dt><dd>{formatValue(split.median)}</dd></div>
      {Object.entries(split.milestone_rates).map(([threshold, rate]) => <div key={threshold}><dt>{threshold} historical rate</dt><dd>{formatRate(rate)}</dd></div>)}
      <div><dt>Avg time on ground</dt><dd>{split.average_time_on_ground_pct == null ? "Unavailable" : `${formatValue(split.average_time_on_ground_pct)}%`}</dd></div>
    </dl>
    {split.games > split.stat_sample_size && <p className="research-coverage-note">{split.games - split.stat_sample_size} games are missing {stat}. The averages and rates do not include those games.</p>}
    <p className="hint">Time on ground recorded in {split.time_on_ground_sample_size} games.</p>
  </article>;
}

export function ContextResults({ research }: { research: PlayerContextResearch }) {
  const adjusted = research.adjusted_effect;
  const tag = research.tag_watch;
  return <>
    <header className="research-header research-results-header"><div className="research-header__copy"><span className="research-eyebrow">Historical player context</span><h2>{research.player_name}</h2><p>{research.team_name ?? "Club unavailable"} · Comparing games with and without {research.teammate_name}</p></div><span className={`research-confidence-badge ${research.confidence.tier === "insufficient_history" || research.confidence.tier === "lower_confidence" ? "research-confidence-badge--limited" : ""}`}>{research.confidence.tier.replaceAll("_", " ")}</span></header>
    {research.evidence.length === 0 && <div className="card" role="status">No recorded match evidence is available for this comparison.</div>}
    <section aria-labelledby="split-heading">
      <h2 id="split-heading">With and without comparison</h2>
      <p className="hint">The API restricts this history to the player’s most recent recorded club. “Without” means no same-club teammate match record was found, not a verified injury or selection status.</p>
      <div className="research-split-grid"><SplitCard title="With teammate" split={research.with_teammate} stat={research.stat} />
        <div className="research-split-difference"><span>Without minus with</span><strong className={`num ${research.raw_difference == null ? "research-value-unavailable" : ""}`}>{formatDifference(research.raw_difference)}</strong><small>{research.stat}</small></div>
        <SplitCard title="Without teammate" split={research.without_teammate} stat={research.stat} /></div>
    </section>
    <section className="card research-interpretation" aria-labelledby="meaning-heading">
      <h2 id="meaning-heading">What this means</h2><p>{explainDifference(research)}</p>
      <h3>Sample-size confidence: {research.confidence.tier.replaceAll("_", " ")}</h3>
      <p>Based on {research.with_teammate.games} games with the teammate and {research.without_teammate.games} without. Confidence is the API’s sample-size assessment, not a probability that the effect is real.</p>
      <ul>{research.confidence.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
      <h3>{adjusted.available && adjusted.value != null ? `Adjusted difference: ${formatDifference(adjusted.value)} ${research.stat}` : "Adjusted difference unavailable"}</h3>
      <p>{adjusted.explanation}</p>
      <p className="hint">Baseline-eligible games: {adjusted.games_with_baseline_teammate_in} with, {adjusted.games_with_baseline_teammate_out} without.</p>
      <details><summary>What the adjustment can account for</summary><ul>{Object.entries(research.confounders).map(([name, note]) => <li key={name}><strong>{name.replaceAll("_", " ")}: {note.considered ? "included in the method" : "not controlled for"}.</strong> {note.method ?? note.reason ?? "No explanation available."}</li>)}</ul></details>
    </section>
    <section className="card research-interpretation" aria-labelledby="availability-heading">
      <h2 id="availability-heading">Role and tagging evidence</h2>
      <h3>Role analysis {research.role_analysis_available ? "status" : "unavailable"}</h3><p>{research.role_analysis_explanation}</p>
      <h3>Tag watch: {tag.status.replaceAll("_", " ")}</h3><p>{tag.explanation}</p>
      {tag.status === "available" && tag.tag_rate != null && <p>Historical verified tag annotation rate: <strong>{formatRate(tag.tag_rate)}</strong>. This is not a likelihood of being tagged next game.</p>}
      <p className="hint">{tag.verified_annotation_count} verified annotations; {tag.games_played == null ? "game count unavailable" : `${tag.games_played} games played`}. No role adjustment or future tag probability is estimated on this page.</p>
    </section>
    <PlayerContextEvidence rows={research.evidence} stat={research.stat} />
  </>;
}

export default function PlayerResearchPage() {
  const [player, setPlayer] = useState<PlayerSummary | null>(null);
  const [teammate, setTeammate] = useState<PlayerSummary | null>(null);
  const [stat, setStat] = useState<ContextStat>("disposals");
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ key: string; loading: boolean; data: PlayerContextResearch | null; error: string | null }>({ key: "", loading: false, data: null, error: null });
  const key = player && teammate ? `${player.id}/${teammate.id}/${stat}/${retry}` : "";
  useEffect(() => {
    if (!player || !teammate || player.id === teammate.id) return;
    const controller = new AbortController();
    setState({ key, loading: true, data: null, error: null });
    fetchPlayerContext(player.id, teammate.id, stat, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ key, loading: false, data, error: null });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ key, loading: false, data: null, error: error instanceof ApiError && error.status === 404 ? `Context data could not be found. The player may be unavailable or the context service may not be deployed yet. ${error.message}` : error instanceof Error ? error.message : "Unable to load player context." });
    });
    return () => controller.abort();
  }, [player, teammate, stat, key]);
  return <main className="player-research-page">
    <header><h1>Player Research</h1><p className="hint">Explore recorded performance with and without a teammate. Select players from the live player directory.</p></header>
    <section className="card research-live-controls" aria-label="Choose comparison">
      <PlayerPicker label="Player" value={player} onChange={next => { setPlayer(next); setTeammate(null); }} />
      <PlayerPicker key={player?.id ?? "none"} label="Teammate" value={teammate} onChange={setTeammate} excludeId={player?.id} />
      <label className="research-select-field">Statistic<select value={stat} onChange={event => setStat(event.target.value as ContextStat)}><option value="disposals">Disposals</option><option value="goals">Goals</option></select></label>
    </section>
    {player && teammate && <div className="research-comparison-actions"><button type="button" onClick={() => { setPlayer(teammate); setTeammate(player); }}>Swap player and teammate</button><p className="hint">Swapping asks how the other player performs. The result may differ.</p></div>}
    {!key ? <p role="status">Select a player and a different teammate to see their recorded comparison.</p> : state.key !== key || state.loading ? <p role="status">Loading player context…</p> : state.error ? <div className="error-banner" role="alert"><p>{state.error}</p><button type="button" onClick={() => setRetry(retry + 1)}>Retry comparison</button></div> : state.data && <ContextResults key={key} research={state.data} />}
    <Disclaimer />
  </main>;
}
