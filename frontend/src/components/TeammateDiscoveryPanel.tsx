import { explainCandidateDifference, formatDifference, formatValue, type ContextStat, type TeammateCandidate, type TeammateDiscoveryResult } from "../features/playerContext";

function CandidateCard({ playerName, stat, candidate, onSelect }: {
  playerName: string; stat: ContextStat; candidate: TeammateCandidate; onSelect: (candidate: TeammateCandidate) => void;
}) {
  const adjusted = candidate.adjusted_effect;
  return <li className={`discovery-candidate ${candidate.sufficient_evidence ? "" : "discovery-candidate--limited"}`}>
    <div className="discovery-candidate__heading">
      <h3>{candidate.teammate_name}</h3>
      <span className={`research-confidence-badge ${candidate.sufficient_evidence ? "" : "research-confidence-badge--limited"}`}>{candidate.confidence.tier.replaceAll("_", " ")}</span>
    </div>
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

export default function TeammateDiscoveryPanel({ playerName, stat, loading, error, discovery, onRetry, onSelect }: {
  playerName: string;
  stat: ContextStat;
  loading: boolean;
  error: string | null;
  discovery: TeammateDiscoveryResult | null;
  onRetry: () => void;
  onSelect: (candidate: TeammateCandidate) => void;
}) {
  if (loading) return <p role="status">Finding teammates worth investigating…</p>;
  if (error) return <div className="error-banner" role="alert"><p>{error}</p><button type="button" onClick={onRetry}>Retry</button></div>;
  if (!discovery) return null;

  if (discovery.candidates.length === 0) {
    return <div className="card research-empty-evidence" role="status">
      <h3>No teammates found</h3>
      <p>{discovery.explanation}</p>
    </div>;
  }

  const sufficient = discovery.candidates.filter(c => c.sufficient_evidence);
  const insufficient = discovery.candidates.filter(c => !c.sufficient_evidence);

  return <div className="discovery-results">
    <p className="hint" role="status">{discovery.explanation}</p>
    {sufficient.length > 0 && <section aria-labelledby="discovery-sufficient-heading">
      <h3 id="discovery-sufficient-heading">Worth investigating ({sufficient.length})</h3>
      <ul className="discovery-candidate-list">{sufficient.map(c => <CandidateCard key={c.teammate_id} playerName={playerName} stat={stat} candidate={c} onSelect={onSelect} />)}</ul>
    </section>}
    {insufficient.length > 0 && <section aria-labelledby="discovery-insufficient-heading">
      <h3 id="discovery-insufficient-heading">Insufficient evidence ({insufficient.length})</h3>
      <p className="hint">These teammates have too few shared or missing games to draw a meaningful comparison from yet, but you can still open the raw evidence.</p>
      <ul className="discovery-candidate-list">{insufficient.map(c => <CandidateCard key={c.teammate_id} playerName={playerName} stat={stat} candidate={c} onSelect={onSelect} />)}</ul>
    </section>}
  </div>;
}
