import { useEffect, useMemo, useState } from "react";
import "./PlayerInsightsPage.css";
import Disclaimer from "../components/Disclaimer";
import { DisposalProjectionTable, GoalProjectionTable } from "../components/ProjectionTable";
import {
  fetchTeams,
  fetchUpcomingProjections,
  type ConfidenceTierLive,
  type DisposalProjection,
  type GoalProjection,
  type Team,
} from "../api/client";

const CONFIDENCE_OPTIONS: { value: ConfidenceTierLive | ""; label: string }[] = [
  { value: "", label: "Any confidence" },
  { value: "higher_confidence", label: "Higher" },
  { value: "moderate_confidence", label: "Moderate" },
  { value: "lower_confidence", label: "Lower" },
  { value: "insufficient_history", label: "Insufficient history" },
];

function PlayerInsightsPage() {
  const [market, setMarket] = useState<"player_disposals" | "player_goals">("player_disposals");
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamId, setTeamId] = useState<number | "">("");
  const [confidence, setConfidence] = useState<ConfidenceTierLive | "">("");
  const [minProbability, setMinProbability] = useState("");
  const [threshold, setThreshold] = useState<string>("20");

  const [disposals, setDisposals] = useState<DisposalProjection[]>([]);
  const [goals, setGoals] = useState<GoalProjection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTeams().then(setTeams).catch(() => setTeams([]));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const minProb = minProbability.trim() === "" ? undefined : Number(minProbability) / 100;
    fetchUpcomingProjections({
      market,
      teamId: teamId === "" ? undefined : teamId,
      confidence: confidence === "" ? undefined : confidence,
      minProbability: minProb,
      threshold: minProb !== undefined ? Number(threshold) : undefined,
    })
      .then((result) => {
        setDisposals(result.disposals ?? []);
        setGoals(result.goals ?? []);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load projections"))
      .finally(() => setLoading(false));
  }, [market, teamId, confidence, minProbability, threshold]);

  const thresholdOptions = market === "player_disposals" ? ["15", "20", "25", "30", "35", "40"] : ["1", "2", "3", "4", "5"];

  const summaryText = useMemo(() => {
    const n = market === "player_disposals" ? disposals.length : goals.length;
    return `${n} player${n === 1 ? "" : "s"} projected for the upcoming round`;
  }, [market, disposals, goals]);

  return (
    <main className="player-insights-page">
      <header className="player-insights-page__header">
        <h1>Player Insights</h1>
        <p className="hint">
          Aggregated player disposal and goal projections across every upcoming match — find the strongest model
          probabilities across the whole round. Historical model research only where noted; live projections here
          are pre-match estimates, not guaranteed outcomes.
        </p>
      </header>

      <section className="player-insights-page__filters">
        <label>
          Market
          <select value={market} onChange={(e) => setMarket(e.target.value as "player_disposals" | "player_goals")}>
            <option value="player_disposals">Disposals</option>
            <option value="player_goals">Goals</option>
          </select>
        </label>
        <label>
          Team
          <select value={teamId} onChange={(e) => setTeamId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">All teams</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Confidence
          <select value={confidence} onChange={(e) => setConfidence(e.target.value as ConfidenceTierLive | "")}>
            {CONFIDENCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Threshold
          <select value={threshold} onChange={(e) => setThreshold(e.target.value)}>
            {thresholdOptions.map((t) => (
              <option key={t} value={t}>
                {t}+
              </option>
            ))}
          </select>
        </label>
        <label>
          Min. probability (%)
          <input
            type="number"
            min="0"
            max="100"
            value={minProbability}
            onChange={(e) => setMinProbability(e.target.value)}
            placeholder="e.g. 50"
          />
        </label>
      </section>

      {loading && <p className="hint">Loading…</p>}
      {error && <div className="player-insights-page__error">{error}</div>}

      {!loading && !error && (
        <section className="backtest-panel">
          <p className="hint">{summaryText}</p>
          {market === "player_disposals" ? (
            <DisposalProjectionTable rows={disposals} showMatchColumn />
          ) : (
            <GoalProjectionTable rows={goals} showMatchColumn />
          )}
        </section>
      )}

      <Disclaimer />
    </main>
  );
}

export default PlayerInsightsPage;
