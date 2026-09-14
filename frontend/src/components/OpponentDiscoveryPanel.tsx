import { explainOpponentCandidateDifference, formatDifference, formatValue, type ContextStat, type OpponentCandidate, type OpponentDiscoveryResult } from "../features/playerContext";

function CandidateCard({ playerName, stat, candidate, onSelect }: {
  playerName: string; stat: ContextStat; candidate: OpponentCandidate; onSelect: (candidate: OpponentCandidate) => void;
}) {
  const adjusted = candidate.adjusted_effect;
  return <li className={`discovery-candidate ${candidate.sufficient_evidence ? "" : "discovery-candidate--limited"}`}>
    <div className="discovery-candidate__heading">
      <h3>{candidate.opponent_team_name}</h3>
      <span className={`research-confidence-badge ${candidate.sufficient_evidence ? "" : "research-confidence-badge--limited"}`}>{candidate.confidence.tier.replaceAll("_", " ")}</span>
    </div>
    <dl className="discovery-candidate__stats">
      <div><dt>Games against</dt><dd>{candidate.against_opponent.games}</dd></div>
      <div><dt>Games vs others</dt><dd>{candidate.against_other_opponents.games}</dd></div>
      <div><dt>Avg {stat} against</dt><dd>{formatValue(candidate.against_opponent.mean)}</dd></div>
      <div><dt>Avg {stat} vs others</dt><dd>{formatValue(candidate.against_other_opponents.mean)}</dd></div>
      <div><dt>Raw difference</dt><dd>{formatDifference(candidate.raw_difference)}</dd></div>
      <div><dt>Adjusted difference</dt><dd>{adjusted.available && adjusted.value != null ? formatDifference(adjusted.value) : "Unavailable"}</dd></div>
    </dl>
    {!candidate.sufficient_evidence && <p className="research-coverage-note">{candidate.confidence.warnings[0] ?? "Not enough recorded games to treat this comparison as more than anecdotal."}</p>}
    <p className="hint">{explainOpponentCandidateDifference(playerName, stat, candidate)}</p>
    <button type="button" onClick={() => onSelect(candidate)}>Compare against {candidate.opponent_team_name}</button>
  </li>;
}

export default function OpponentDiscoveryPanel({ playerName, stat, loading, error, discovery, onRetry, onSelect }: {
  playerName: string;
  stat: ContextStat;
  loading: boolean;
  error: string | null;
  discovery: OpponentDiscoveryResult | null;
  onRetry: () => void;
  onSelect: (candidate: OpponentCandidate) => void;
}) {
  if (loading) return <p role="status">Finding opponents worth investigating…</p>;
  if (error) return <div className="error-banner" role="alert"><p>{error}</p><button type="button" onClick={onRetry}>Retry</button></div>;
  if (!discovery) return null;

  if (discovery.candidates.length === 0) {
    return <div className="card research-empty-evidence" role="status">
      <h3>No opponent history found</h3>
      <p>{discovery.explanation}</p>
    </div>;
  }

  const sufficient = discovery.candidates.filter(c => c.sufficient_evidence);
  const insufficient = discovery.candidates.filter(c => !c.sufficient_evidence);

  return <div className="discovery-results">
    <p className="hint" role="status">{discovery.explanation}</p>
    <p className="hint">{discovery.scope_explanation}</p>
    {sufficient.length > 0 && <section aria-labelledby="opponent-discovery-sufficient-heading">
      <h3 id="opponent-discovery-sufficient-heading">Worth investigating ({sufficient.length})</h3>
      <ul className="discovery-candidate-list">{sufficient.map(c => <CandidateCard key={c.opponent_team_id} playerName={playerName} stat={stat} candidate={c} onSelect={onSelect} />)}</ul>
    </section>}
    {insufficient.length > 0 && <section aria-labelledby="opponent-discovery-insufficient-heading">
      <h3 id="opponent-discovery-insufficient-heading">Insufficient evidence ({insufficient.length})</h3>
      <p className="hint">These opponents have too few recorded games to draw a meaningful comparison from yet, but you can still open the raw evidence.</p>
      <ul className="discovery-candidate-list">{insufficient.map(c => <CandidateCard key={c.opponent_team_id} playerName={playerName} stat={stat} candidate={c} onSelect={onSelect} />)}</ul>
    </section>}
  </div>;
}
