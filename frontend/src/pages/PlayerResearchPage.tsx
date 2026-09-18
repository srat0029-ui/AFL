import { useEffect, useState } from "react";
import PlayerContextEvidence from "../components/PlayerContextEvidence";
import PlayerHistoryChart from "../components/PlayerHistoryChart";
import TeammateDiscoveryPanel from "../components/TeammateDiscoveryPanel";
import OpponentDiscoveryPanel from "../components/OpponentDiscoveryPanel";
import OpponentContextEvidence from "../components/OpponentContextEvidence";
import Disclaimer from "../components/Disclaimer";
import PageHeader from "../components/ui/PageHeader";
import EmptyState from "../components/ui/EmptyState";
import { ApiError, fetchOpponentContext, fetchOpponentDiscovery, fetchPlayer, fetchPlayerContext, fetchPlayers, fetchTeammateDiscovery, type PlayerSummary } from "../api/client";
import { explainDifference, explainOpponentDifference, formatDifference, formatRate, formatValue, type ContextSplit, type ContextStat, type OpponentCandidate, type OpponentContextResearch, type OpponentDiscoveryResult, type PlayerContextResearch, type TeammateCandidate, type TeammateDiscoveryResult } from "../features/playerContext";
import "./PlayerResearchPage.css";

type ResearchMode = "teammates" | "opponents";

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
    <section aria-labelledby="history-heading">
      <h2 id="history-heading">Visual history</h2>
      <p className="hint">Every recorded game, oldest to newest, coloured by whether {research.teammate_name} played that game too.</p>
      <PlayerHistoryChart
        points={research.evidence.map((g) => ({ match_id: g.match_id, scheduled_start: g.scheduled_start, stat_value: g.stat_value, highlighted: g.teammate_played }))}
        stat={research.stat}
        highlightedLabel="With teammate"
        otherLabel="Without teammate"
      />
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

export function OpponentContextResults({ research }: { research: OpponentContextResearch }) {
  const adjusted = research.adjusted_effect;
  return <>
    <header className="research-header research-results-header"><div className="research-header__copy"><span className="research-eyebrow">Historical opponent context</span><h2>{research.player_name}</h2><p>{research.team_name ?? "Club unavailable"} · Comparing games against {research.opponent_team_name} with games against other opponents</p></div><span className={`research-confidence-badge ${research.confidence.tier === "insufficient_history" || research.confidence.tier === "lower_confidence" ? "research-confidence-badge--limited" : ""}`}>{research.confidence.tier.replaceAll("_", " ")}</span></header>
    {research.evidence.length === 0 && <div className="card" role="status">No recorded match evidence is available for this comparison.</div>}
    <section aria-labelledby="opponent-split-heading">
      <h2 id="opponent-split-heading">Against opponent vs other opponents</h2>
      <p className="hint">{research.scope_explanation} “Other opponents” means every other recorded opponent this player has faced at that club, not one specific rival.</p>
      <div className="research-split-grid"><SplitCard title={`Against ${research.opponent_team_name}`} split={research.against_opponent} stat={research.stat} />
        <div className="research-split-difference"><span>Against minus other opponents</span><strong className={`num ${research.raw_difference == null ? "research-value-unavailable" : ""}`}>{formatDifference(research.raw_difference)}</strong><small>{research.stat}</small></div>
        <SplitCard title="Against other opponents" split={research.against_other_opponents} stat={research.stat} /></div>
    </section>
    <section aria-labelledby="opponent-history-heading">
      <h2 id="opponent-history-heading">Visual history</h2>
      <p className="hint">Every recorded game, oldest to newest, coloured by whether it was against {research.opponent_team_name}.</p>
      <PlayerHistoryChart
        points={research.evidence.map((g) => ({ match_id: g.match_id, scheduled_start: g.scheduled_start, stat_value: g.stat_value, highlighted: g.is_selected_opponent }))}
        stat={research.stat}
        highlightedLabel={`Against ${research.opponent_team_name}`}
        otherLabel="Other opponents"
      />
    </section>
    <section className="card research-interpretation" aria-labelledby="opponent-meaning-heading">
      <h2 id="opponent-meaning-heading">What this means</h2><p>{explainOpponentDifference(research)}</p>
      <h3>Sample-size confidence: {research.confidence.tier.replaceAll("_", " ")}</h3>
      <p>Based on {research.against_opponent.games} games against {research.opponent_team_name} and {research.against_other_opponents.games} against other opponents. Confidence is the API’s sample-size assessment, not a probability that the effect is real.</p>
      <ul>{research.confidence.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
      <h3>{adjusted.available && adjusted.value != null ? `Adjusted difference: ${formatDifference(adjusted.value)} ${research.stat}` : "Adjusted difference unavailable"}</h3>
      <p>{adjusted.explanation}</p>
      <p className="hint">Baseline-eligible games: {adjusted.games_with_baseline_against_opponent} against this opponent, {adjusted.games_with_baseline_other_opponents} against other opponents.</p>
      <details><summary>What the adjustment can account for</summary><ul>{Object.entries(research.confounders).map(([name, note]) => <li key={name}><strong>{name.replaceAll("_", " ")}: {note.considered ? "included in the method" : "not controlled for"}.</strong> {note.method ?? note.reason ?? "No explanation available."}</li>)}</ul></details>
    </section>
    <section className="card research-interpretation" aria-labelledby="opponent-role-heading">
      <h2 id="opponent-role-heading">Role evidence</h2>
      <h3>Role analysis {research.role_analysis_available ? "status" : "unavailable"}</h3><p>{research.role_analysis_explanation}</p>
    </section>
    <OpponentContextEvidence rows={research.evidence} stat={research.stat} selectedOpponentName={research.opponent_team_name} />
  </>;
}

export default function PlayerResearchPage() {
  const [mode, setMode] = useState<ResearchMode>("teammates");
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

  // Teammate discovery - who is worth investigating for the selected
  // player, before a specific teammate has been chosen.
  const [discoveryRetry, setDiscoveryRetry] = useState(0);
  const discoveryKey = player ? `${player.id}/${stat}/${discoveryRetry}` : "";
  const [discoveryState, setDiscoveryState] = useState<{ key: string; loading: boolean; data: TeammateDiscoveryResult | null; error: string | null }>({ key: "", loading: false, data: null, error: null });
  useEffect(() => {
    if (!player) { setDiscoveryState({ key: "", loading: false, data: null, error: null }); return; }
    const controller = new AbortController();
    setDiscoveryState({ key: discoveryKey, loading: true, data: null, error: null });
    fetchTeammateDiscovery(player.id, stat, controller.signal).then(data => {
      if (!controller.signal.aborted) setDiscoveryState({ key: discoveryKey, loading: false, data, error: null });
    }).catch(error => {
      if (!controller.signal.aborted) setDiscoveryState({ key: discoveryKey, loading: false, data: null, error: error instanceof Error ? error.message : "Unable to load teammate suggestions." });
    });
    return () => controller.abort();
  }, [player, stat, discoveryKey]);

  const [resolvingCandidateId, setResolvingCandidateId] = useState<number | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);
  async function handleSelectCandidate(candidate: TeammateCandidate) {
    setResolvingCandidateId(candidate.teammate_id);
    setResolveError(null);
    try {
      const full = await fetchPlayer(candidate.teammate_id);
      if (full) setTeammate(full);
      else setResolveError(`${candidate.teammate_name} could not be loaded from the player directory.`);
    } catch (error) {
      setResolveError(error instanceof Error ? error.message : "Could not load that teammate.");
    } finally {
      setResolvingCandidateId(null);
    }
  }

  // Opponent context - "against this opponent vs against other opponents".
  const [opponent, setOpponent] = useState<{ id: number; name: string } | null>(null);
  const [opponentRetry, setOpponentRetry] = useState(0);
  const [opponentResultState, setOpponentResultState] = useState<{ key: string; loading: boolean; data: OpponentContextResearch | null; error: string | null }>({ key: "", loading: false, data: null, error: null });
  const opponentKey = player && opponent ? `${player.id}/${opponent.id}/${stat}/${opponentRetry}` : "";
  useEffect(() => {
    if (mode !== "opponents" || !player || !opponent) return;
    const controller = new AbortController();
    setOpponentResultState({ key: opponentKey, loading: true, data: null, error: null });
    fetchOpponentContext(player.id, opponent.id, stat, controller.signal).then(data => {
      if (!controller.signal.aborted) setOpponentResultState({ key: opponentKey, loading: false, data, error: null });
    }).catch(error => {
      if (!controller.signal.aborted) setOpponentResultState({ key: opponentKey, loading: false, data: null, error: error instanceof ApiError && error.status === 404 ? `Opponent context could not be found. ${error.message}` : error instanceof Error ? error.message : "Unable to load opponent context." });
    });
    return () => controller.abort();
  }, [mode, player, opponent, stat, opponentKey]);

  // Opponent discovery - which opponents are worth investigating for the
  // selected player, before a specific opponent has been chosen.
  const [opponentDiscoveryRetry, setOpponentDiscoveryRetry] = useState(0);
  const opponentDiscoveryKey = mode === "opponents" && player ? `${player.id}/${stat}/${opponentDiscoveryRetry}` : "";
  const [opponentDiscoveryState, setOpponentDiscoveryState] = useState<{ key: string; loading: boolean; data: OpponentDiscoveryResult | null; error: string | null }>({ key: "", loading: false, data: null, error: null });
  useEffect(() => {
    if (mode !== "opponents" || !player) { setOpponentDiscoveryState({ key: "", loading: false, data: null, error: null }); return; }
    const controller = new AbortController();
    setOpponentDiscoveryState({ key: opponentDiscoveryKey, loading: true, data: null, error: null });
    fetchOpponentDiscovery(player.id, stat, controller.signal).then(data => {
      if (!controller.signal.aborted) setOpponentDiscoveryState({ key: opponentDiscoveryKey, loading: false, data, error: null });
    }).catch(error => {
      if (!controller.signal.aborted) setOpponentDiscoveryState({ key: opponentDiscoveryKey, loading: false, data: null, error: error instanceof Error ? error.message : "Unable to load opponent suggestions." });
    });
    return () => controller.abort();
  }, [mode, player, stat, opponentDiscoveryKey]);

  function handleSelectOpponentCandidate(candidate: OpponentCandidate) {
    setOpponent({ id: candidate.opponent_team_id, name: candidate.opponent_team_name });
  }

  function handleModeChange(next: ResearchMode) {
    setMode(next);
  }

  return <main className="player-research-page">
    <PageHeader
      eyebrow="Players"
      title="Compare teammates & opponents"
      description="Pick a player to see whether their numbers genuinely change with a specific teammate on the field, or against a specific opponent — with sample sizes and confidence shown honestly, never a raw effect size alone."
    />
    <section className="card research-live-controls" aria-label="Choose comparison">
      <PlayerPicker label="Player" value={player} onChange={next => { setPlayer(next); setTeammate(null); setOpponent(null); setResolveError(null); }} />
      {mode === "teammates" && <PlayerPicker key={player?.id ?? "none"} label="Teammate" value={teammate} onChange={setTeammate} excludeId={player?.id} />}
      <label className="research-select-field">Statistic<select value={stat} onChange={event => setStat(event.target.value as ContextStat)}><option value="disposals">Disposals</option><option value="goals">Goals</option></select></label>
    </section>
    {player && <div className="research-filter-buttons" role="group" aria-label="Choose research mode">
      <button type="button" aria-pressed={mode === "teammates"} className={mode === "teammates" ? "is-active" : ""} onClick={() => handleModeChange("teammates")}>Teammates</button>
      <button type="button" aria-pressed={mode === "opponents"} className={mode === "opponents" ? "is-active" : ""} onClick={() => handleModeChange("opponents")}>Opponents</button>
    </div>}
    {mode === "teammates" && <>
      {player && teammate && <div className="research-comparison-actions"><button type="button" onClick={() => { setPlayer(teammate); setTeammate(player); }}>Swap player and teammate</button><button type="button" onClick={() => setTeammate(null)}>Choose a different teammate</button><p className="hint">Swapping asks how the other player performs. The result may differ.</p></div>}
      {player && !teammate && <section aria-labelledby="discovery-heading">
        <div className="section-row research-section-heading"><div><h2 id="discovery-heading">Teammates worth investigating</h2><p className="hint">Ranked by evidence sufficiency and shared-match sample size — never by the size of a statistical difference. Or search for a specific teammate above.</p></div></div>
        <TeammateDiscoveryPanel playerName={player.display_name} stat={stat} loading={discoveryState.loading || discoveryState.key !== discoveryKey} error={discoveryState.error} discovery={discoveryState.data} onRetry={() => setDiscoveryRetry(r => r + 1)} onSelect={handleSelectCandidate} />
        {resolvingCandidateId != null && <p role="status">Loading teammate…</p>}
        {resolveError && <div className="error-banner" role="alert"><p>{resolveError}</p></div>}
      </section>}
      {!player ? <EmptyState title="Choose a player to get started" description="Search for a player above to see which teammates are worth investigating, then compare their numbers with and without that teammate on the field." /> : !teammate ? null : state.key !== key || state.loading ? <p role="status">Loading player context…</p> : state.error ? <div className="error-banner" role="alert"><p>{state.error}</p><button type="button" onClick={() => setRetry(retry + 1)}>Retry comparison</button></div> : state.data && <ContextResults key={key} research={state.data} />}
    </>}
    {mode === "opponents" && <>
      {player && opponent && <div className="research-comparison-actions"><button type="button" onClick={() => setOpponent(null)}>Choose a different opponent</button></div>}
      {player && !opponent && <section aria-labelledby="opponent-discovery-heading">
        <div className="section-row research-section-heading"><div><h2 id="opponent-discovery-heading">Opponents worth investigating</h2><p className="hint">Ranked by evidence sufficiency and sample size — never by the size of a statistical difference.</p></div></div>
        <OpponentDiscoveryPanel playerName={player.display_name} stat={stat} loading={opponentDiscoveryState.loading || opponentDiscoveryState.key !== opponentDiscoveryKey} error={opponentDiscoveryState.error} discovery={opponentDiscoveryState.data} onRetry={() => setOpponentDiscoveryRetry(r => r + 1)} onSelect={handleSelectOpponentCandidate} />
      </section>}
      {!player ? <EmptyState title="Choose a player to get started" description="Search for a player above to see which opponents are worth investigating, then compare their numbers against that opponent versus everyone else." /> : !opponent ? null : opponentResultState.key !== opponentKey || opponentResultState.loading ? <p role="status">Loading opponent context…</p> : opponentResultState.error ? <div className="error-banner" role="alert"><p>{opponentResultState.error}</p><button type="button" onClick={() => setOpponentRetry(r => r + 1)}>Retry comparison</button></div> : opponentResultState.data && <OpponentContextResults key={opponentKey} research={opponentResultState.data} />}
    </>}
    <Disclaimer />
  </main>;
}
