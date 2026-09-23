import { explainCandidateDifference, formatDifference, formatValue, togetherApartLabel, windowActionLabel, type ContextStat, type ContextWindowKey, type TeammateCandidate, type TeammateDiscoveryResult } from "../features/playerContext";

function CandidateCard({ playerName, stat, candidate, windowKey, onSelect }: {
  playerName: string; stat: ContextStat; candidate: TeammateCandidate; windowKey: ContextWindowKey; onSelect: (candidate: TeammateCandidate) => void;
}) {
  const adjusted = candidate.adjusted_effect;
  return <li className={`discovery-candidate ${candidate.sufficient_evidence ? "" : "discovery-candidate--limited"}`}>
    <div className="discovery-candidate__heading">
      <h3>{candidate.teammate_name}</h3>
      <span className={`research-confidence-badge ${candidate.sufficient_evidence ? "" : "research-confidence-badge--limited"}`}>{candidate.confidence.tier.replaceAll("_", " ")}</span>
    </div>
    <p className="discovery-candidate__sample">{togetherApartLabel(candidate.with_teammate.games, candidate.without_teammate.games, windowKey)}{(candidate.games_excluded_outside_tenure ?? 0) > 0 && <span className="discovery-candidate__excluded"> · {candidate.games_excluded_outside_tenure} earlier/out-of-tenure excluded</span>}</p>
    <dl className="discovery-candidate__stats">
      <div><dt>Shared matches</dt><dd>{candidate.with_teammate.games}</dd></div>
      <div><dt>Games without</dt><dd>{candidate.without_teammate.games}</dd></div>
      <div><dt>Avg {stat} with</dt><dd>{formatValue(candidate.with_teammate.mean)}</dd></div>
      <div><dt>Avg {stat} without</dt><dd>{formatValue(candidate.without_teammate.mean)}</dd></div>
      <div><dt>Raw difference</dt><dd>{formatDifference(candidate.raw_difference)}</dd></div>
      <div><dt>Adjusted difference</dt><dd>{adjusted.available && adjusted.value != null ? formatDifference(adjusted.value) : "Unavailable"}</dd></div>
    </dl>
    {!candidate.sufficient_evidence && <p className="research-coverage-note">{candidate.confidence.warnings[0] ?? "Not enough recorded games to treat this comparison as more than anecdotal."}</p>}
    <p className="hint">{explainCandidateDifference(playerName, stat, candidate)}</p>
    <button type="button" onClick={() => onSelect(candidate)}>Compare with {candidate.teammate_name}</button>
  </li>;
}

/** When the active window has no usable candidates, say where usable ones exist - never switch automatically. */
function WindowHints({ discovery, onChangeWindow }: { discovery: TeammateDiscoveryResult; onChangeWindow?: (next: ContextWindowKey) => void }) {
  const broader = discovery.window_options.filter(o => o.key !== discovery.window.key && o.n_sufficient_candidates > 0 && o.games_considered > discovery.window.games_considered);
  if (broader.length === 0 || !onChangeWindow) return null;
  return <div className="research-window-notice__actions">{broader.map(o => <button key={o.key} type="button" onClick={() => onChangeWindow(o.key)}>{windowActionLabel(o.key)} ({o.n_sufficient_candidates} worth investigating)</button>)}</div>;
}

export default function TeammateDiscoveryPanel({ playerName, stat, loading, error, discovery, onRetry, onSelect, onChangeWindow }: {
  playerName: string;
  stat: ContextStat;
  loading: boolean;
  error: string | null;
  discovery: TeammateDiscoveryResult | null;
  onRetry: () => void;
  onSelect: (candidate: TeammateCandidate) => void;
  onChangeWindow?: (next: ContextWindowKey) => void;
}) {
  if (loading) return <p role="status">Finding teammates worth investigating…</p>;
  if (error) return <div className="error-banner" role="alert"><p>{error}</p><button type="button" onClick={onRetry}>Retry</button></div>;
  if (!discovery) return null;

  if (discovery.candidates.length === 0) {
    return <div className="card research-empty-evidence" role="status">
      <h3>No teammates found</h3>
      <p>{discovery.explanation}</p>
      <WindowHints discovery={discovery} onChangeWindow={onChangeWindow} />
    </div>;
  }

  const sufficient = discovery.candidates.filter(c => c.sufficient_evidence);
  const insufficient = discovery.candidates.filter(c => !c.sufficient_evidence);

  return <div className="discovery-results">
    <p className="research-scope" role="status"><strong>{discovery.window.scope_label}</strong> · {discovery.window.games_considered} games considered</p>
    <p className="hint">{discovery.explanation}</p>
    {sufficient.length === 0 && <div className="research-window-notice" role="status"><p>No teammate has enough games both together and apart in this scope.</p><WindowHints discovery={discovery} onChangeWindow={onChangeWindow} /></div>}
    {sufficient.length > 0 && <section aria-labelledby="discovery-sufficient-heading">
      <h3 id="discovery-sufficient-heading">Worth investigating ({sufficient.length})</h3>
      <ul className="discovery-candidate-list">{sufficient.map(c => <CandidateCard key={c.teammate_id} playerName={playerName} stat={stat} candidate={c} windowKey={discovery.window.key} onSelect={onSelect} />)}</ul>
    </section>}
    {insufficient.length > 0 && <section aria-labelledby="discovery-insufficient-heading">
      <h3 id="discovery-insufficient-heading">Insufficient evidence ({insufficient.length})</h3>
      <p className="hint">These teammates have too few shared or missing games to draw a meaningful comparison from yet, but you can still open the raw evidence.</p>
      <ul className="discovery-candidate-list">{insufficient.map(c => <CandidateCard key={c.teammate_id} playerName={playerName} stat={stat} candidate={c} windowKey={discovery.window.key} onSelect={onSelect} />)}</ul>
    </section>}
  </div>;
}
