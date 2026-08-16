import { useEffect, useState } from "react";
import "./PlayerPropPanel.css";
import {
  createPlayerProp,
  deletePlayerProp,
  fetchMatchPlayerProps,
  fetchMatchProjections,
  type DisposalProjection,
  type GoalProjection,
  type PlayerPropLineType,
  type PlayerPropMarketQuote,
  type PlayerPropMarketType,
} from "../api/client";

interface PlayerPropPanelProps {
  matchId: number;
}

function PlayerPropPanel({ matchId }: PlayerPropPanelProps) {
  const [props, setProps] = useState<PlayerPropMarketQuote[]>([]);
  const [disposalRows, setDisposalRows] = useState<DisposalProjection[]>([]);
  const [goalRows, setGoalRows] = useState<GoalProjection[]>([]);
  const [loading, setLoading] = useState(true);

  const [bookmakerName, setBookmakerName] = useState("");
  const [marketType, setMarketType] = useState<PlayerPropMarketType>("player_disposals");
  const [playerId, setPlayerId] = useState<number | "">("");
  const [lineType, setLineType] = useState<PlayerPropLineType>("over_under");
  const [threshold, setThreshold] = useState("");
  const [priceDecimal, setPriceDecimal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [propsData, projections] = await Promise.all([fetchMatchPlayerProps(matchId), fetchMatchProjections(matchId)]);
      setProps(propsData);
      setDisposalRows(projections?.disposals ?? []);
      setGoalRows(projections?.goals ?? []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  const candidatePlayers = marketType === "player_disposals" ? disposalRows : goalRows;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const price = Number(priceDecimal);
    const line = Number(threshold);
    if (playerId === "") {
      setError("Select a player.");
      return;
    }
    if (!Number.isFinite(price) || price <= 1.0) {
      setError("Price must be a decimal odds value greater than 1.0.");
      return;
    }
    if (!Number.isFinite(line)) {
      setError("Enter a valid threshold/line.");
      return;
    }
    setSubmitting(true);
    try {
      await createPlayerProp(matchId, {
        bookmaker_name: bookmakerName.trim(),
        player_id: playerId,
        market_type: marketType,
        line_type: lineType,
        threshold: line,
        price_decimal: price,
      });
      setThreshold("");
      setPriceDecimal("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save prop quote");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    await deletePlayerProp(id);
    await load();
  }

  return (
    <section className="prop-panel">
      <h3>Player prop markets</h3>
      <p className="hint">
        Enter bookmaker player-prop offers manually — no automated bookmaker integration is used. Compare them
        against model probabilities on the Prop Insights page.
      </p>

      <form className="prop-form" onSubmit={handleSubmit}>
        <div className="prop-form__row">
          <label>
            Bookmaker
            <input value={bookmakerName} onChange={(e) => setBookmakerName(e.target.value)} placeholder="e.g. Sportsbet" required />
          </label>
          <label>
            Market
            <select
              value={marketType}
              onChange={(e) => {
                setMarketType(e.target.value as PlayerPropMarketType);
                setPlayerId("");
                setLineType(e.target.value === "player_goals" ? "multi_plus" : "over_under");
              }}
            >
              <option value="player_disposals">Disposals</option>
              <option value="player_goals">Goals</option>
            </select>
          </label>
        </div>
        <div className="prop-form__row">
          <label className="prop-form__player">
            Player
            <select value={playerId} onChange={(e) => setPlayerId(Number(e.target.value))} required>
              <option value="">Select player…</option>
              {candidatePlayers.map((p) => (
                <option key={p.player_id} value={p.player_id}>
                  {p.player_name} (expected {p.expected.toFixed(marketType === "player_goals" ? 2 : 1)})
                </option>
              ))}
            </select>
          </label>
          <label>
            Line type
            <select value={lineType} onChange={(e) => setLineType(e.target.value as PlayerPropLineType)}>
              <option value="over_under">Over/under</option>
              <option value="multi_plus">N+</option>
            </select>
          </label>
          <label>
            Threshold
            <input
              type="number"
              step="0.5"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder={lineType === "multi_plus" ? "e.g. 2" : "e.g. 29.5"}
              required
            />
          </label>
          <label>
            Price (decimal odds)
            <input type="number" step="0.01" min="1.01" value={priceDecimal} onChange={(e) => setPriceDecimal(e.target.value)} placeholder="e.g. 1.90" required />
          </label>
        </div>
        {error && <p className="prop-panel__error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : "Add prop quote"}
        </button>
      </form>

      <h4>Recorded prop quotes</h4>
      {loading && <p className="hint">Loading…</p>}
      {!loading && props.length === 0 && <p className="hint">No player prop quotes recorded for this match yet.</p>}
      {!loading && props.length > 0 && (
        <div className="prop-table-scroll">
          <table className="prop-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Bookmaker</th>
                <th>Market</th>
                <th>Line</th>
                <th>Price</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {props.map((p) => (
                <tr key={p.id}>
                  <td>{p.player_name}</td>
                  <td>{p.bookmaker_name}</td>
                  <td>{p.market_type === "player_disposals" ? "Disposals" : "Goals"}</td>
                  <td>{p.line_type === "multi_plus" ? `${p.threshold.toFixed(1)}+` : `o/u ${p.threshold.toFixed(1)}`}</td>
                  <td>${p.price_decimal.toFixed(2)}</td>
                  <td>
                    <button type="button" className="prop-table__delete" onClick={() => handleDelete(p.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default PlayerPropPanel;
