import { useEffect, useState } from "react";
import {
  fetchCurrentRoundPricing,
  fetchMatchPricing,
  fetchPlayerMarketIntelligence,
  fetchTeamMarketIntelligence,
  type DisposalPriceV1,
  type GoalPriceV1,
  type MarketIntelligence,
  type MatchPricing,
} from "../api/client";
import "./B2BDemoPage.css";

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function fmtOdds(n: number): string {
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function ProvenanceBar({ p, isStale, staleReasons }: { p: { model_name: string; model_version: string; generated_at: string; data_cutoff: string }; isStale?: boolean; staleReasons?: string[] }) {
  return (
    <div className="b2b-demo-provenance">
      <span>
        <strong>{p.model_name}</strong> · {p.model_version}
      </span>
      <span className="hint">generated {new Date(p.generated_at).toLocaleString()}</span>
      <span className="hint">data cutoff {new Date(p.data_cutoff).toLocaleString()}</span>
      {isStale !== undefined && (
        <span className={isStale ? "chip chip--warning" : "chip chip--success"}>
          {isStale ? `stale: ${staleReasons?.join("; ")}` : "fresh"}
        </span>
      )}
    </div>
  );
}

function MarketCompareInline({ intel }: { intel: MarketIntelligence | null | undefined }) {
  if (intel === undefined) return null;
  if (intel === null) return <span className="hint">no market comparison yet</span>;
  if (!intel.has_market) return <span className="hint">no live bookmaker market for this selection</span>;
  return (
    <span className="b2b-demo-market-compare">
      model {fmtPct(intel.model_probability)} vs consensus {intel.market_implied_probability !== null ? fmtPct(intel.market_implied_probability) : "—"}
      {intel.difference_pp !== null && (
        <span className={intel.difference_pp >= 0 ? "prop-insights-table__diff-pos" : "prop-insights-table__diff-neg"}>
          {" "}
          ({intel.difference_pp >= 0 ? "+" : ""}
          {(intel.difference_pp * 100).toFixed(1)}pp)
        </span>
      )}
      {intel.best_price !== null && ` · best price ${intel.best_price.toFixed(2)} (${intel.best_bookmaker})`}
    </span>
  );
}

function DisposalRow({ d, intel, onCompare }: { d: DisposalPriceV1; intel: MarketIntelligence | null | undefined; onCompare: () => void }) {
  const key20 = d.thresholds.find((t) => t.threshold === 20.5);
  const key25 = d.thresholds.find((t) => t.threshold === 25.5);
  const key30 = d.thresholds.find((t) => t.threshold === 30.5);
  return (
    <tr>
      <td>{d.player_name}</td>
      <td>{d.expected.toFixed(1)}</td>
      <td>{key20 ? fmtPct(key20.probability) : "—"}</td>
      <td>{key25 ? fmtPct(key25.probability) : "—"}</td>
      <td>{key30 ? fmtPct(key30.probability) : "—"}</td>
      <td>{d.confidence_tier.replace("_confidence", "")}</td>
      <td>{d.lineup_status}</td>
      <td>{d.is_stale ? "stale" : "fresh"}</td>
      <td>{d.calibration ? `ECE ${d.calibration.ece.toFixed(3)} (n=${d.calibration.n.toLocaleString()})` : "—"}</td>
      <td>
        {intel === undefined ? (
          <button type="button" className="b2b-demo-compare-btn" onClick={onCompare}>
            Compare vs market
          </button>
        ) : (
          <MarketCompareInline intel={intel} />
        )}
      </td>
    </tr>
  );
}

function GoalRow({ g }: { g: GoalPriceV1 }) {
  const p1 = g.thresholds.find((t) => t.threshold === 0.5);
  const p2 = g.thresholds.find((t) => t.threshold === 1.5);
  const p3 = g.thresholds.find((t) => t.threshold === 2.5);
  return (
    <tr>
      <td>{g.player_name}</td>
      <td>{g.expected.toFixed(2)}</td>
      <td>{p1 ? fmtPct(p1.probability) : "—"}</td>
      <td>{p2 ? fmtPct(p2.probability) : "—"}</td>
      <td>{p3 ? fmtPct(p3.probability) : "—"}</td>
      <td>{g.confidence_tier.replace("_confidence", "")}</td>
      <td>{g.is_stale ? "stale" : "fresh"}</td>
    </tr>
  );
}

const DISPOSAL_LIMIT = 12;
const GOAL_LIMIT = 12;

function B2BDemoPage() {
  const [pricing, setPricing] = useState<MatchPricing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [teamIntel, setTeamIntel] = useState<MarketIntelligence | null>(null);
  const [disposalIntel, setDisposalIntel] = useState<Record<number, MarketIntelligence | null>>({});

  useEffect(() => {
    fetchCurrentRoundPricing()
      .then((round) => {
        const matchId = round.teams[0]?.match_id;
        if (!matchId) {
          setError("No upcoming match currently available to demo.");
          return null;
        }
        return fetchMatchPricing(matchId);
      })
      .then((mp) => {
        if (!mp) return;
        setPricing(mp);
        fetchTeamMarketIntelligence(mp.match_id, "h2h", mp.team.home_team)
          .then(setTeamIntel)
          .catch(() => setTeamIntel(null));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load demo pricing"));
  }, []);

  function compareDisposal(d: DisposalPriceV1) {
    const t = d.thresholds.find((x) => x.threshold === 20.5);
    if (!t || !pricing) return;
    fetchPlayerMarketIntelligence(d.player_id, "player_disposals", pricing.match_id, t.threshold)
      .then((intel) => setDisposalIntel((prev) => ({ ...prev, [d.player_id]: intel })))
      .catch(() => setDisposalIntel((prev) => ({ ...prev, [d.player_id]: null })));
  }

  return (
    <main className="b2b-demo-page">
      <h1>B2B Pricing Demo</h1>
      <p className="subtitle">
        One upcoming match, priced end-to-end by the engine — team markets, player disposal/goal prices at multiple
        thresholds, model provenance, calibration, lineup/data freshness, and a live market comparison where a
        bookmaker quote exists. Read-only; nothing on this page trains or changes a model.
      </p>

      {error && <p className="b2b-demo-page__error">{error}</p>}
      {!pricing && !error && <p>Loading…</p>}

      {pricing && (
        <>
          <section className="b2b-demo-section">
            <h2>
              {pricing.team.home_team} vs {pricing.team.away_team}
            </h2>
            <ProvenanceBar p={pricing.team.provenance} />
            <table className="b2b-demo-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Probability</th>
                  <th>Fair odds</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{pricing.team.home_team} win</td>
                  <td>{fmtPct(pricing.team.home_win_probability)}</td>
                  <td>{fmtOdds(pricing.team.home_fair_odds)}</td>
                </tr>
                <tr>
                  <td>Draw</td>
                  <td>{fmtPct(pricing.team.draw_probability)}</td>
                  <td>{fmtOdds(pricing.team.draw_fair_odds)}</td>
                </tr>
                <tr>
                  <td>{pricing.team.away_team} win</td>
                  <td>{fmtPct(pricing.team.away_win_probability)}</td>
                  <td>{fmtOdds(pricing.team.away_fair_odds)}</td>
                </tr>
              </tbody>
            </table>
            <p className="hint">
              Expected margin {pricing.team.expected_margin.toFixed(1)} · expected total {pricing.team.expected_total_points.toFixed(1)}
            </p>
            <p className="b2b-demo-market-compare-block">
              Market comparison ({pricing.team.home_team} to win): <MarketCompareInline intel={teamIntel} />
            </p>
          </section>

          <section className="b2b-demo-section">
            <h2>Player disposal prices</h2>
            <p className="hint">Showing up to {DISPOSAL_LIMIT} of {pricing.disposals.length} players. Click "Compare vs market" for a live line.</p>
            <div className="b2b-demo-table__wrap">
              <table className="b2b-demo-table">
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Expected</th>
                    <th>20.5+</th>
                    <th>25.5+</th>
                    <th>30.5+</th>
                    <th>Confidence</th>
                    <th>Lineup</th>
                    <th>Freshness</th>
                    <th>Calibration</th>
                    <th>Market</th>
                  </tr>
                </thead>
                <tbody>
                  {pricing.disposals.slice(0, DISPOSAL_LIMIT).map((d) => (
                    <DisposalRow key={d.player_id} d={d} intel={disposalIntel[d.player_id]} onCompare={() => compareDisposal(d)} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="b2b-demo-section">
            <h2>Player goal prices</h2>
            <p className="hint">Showing up to {GOAL_LIMIT} of {pricing.goals.length} players.</p>
            <div className="b2b-demo-table__wrap">
              <table className="b2b-demo-table">
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Expected</th>
                    <th>0.5+</th>
                    <th>1.5+</th>
                    <th>2.5+</th>
                    <th>Confidence</th>
                    <th>Freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {pricing.goals.slice(0, GOAL_LIMIT).map((g) => (
                    <GoalRow key={g.player_id} g={g} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

export default B2BDemoPage;
