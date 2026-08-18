import { useEffect, useMemo, useState } from "react";
import "./OddsPanel.css";
import {
  createOdds,
  deleteOdds,
  fetchBookmakers,
  fetchEdges,
  fetchOdds,
  type MarketEdge,
  type MarketType,
  type OddsQuote,
} from "../api/client";

const MARKET_LABELS: Record<MarketType, string> = {
  h2h: "Match winner (H2H)",
  line: "Line / handicap",
  total: "Total points",
};

const EDGE_LABELS: Record<MarketEdge["edge_tier"], string> = {
  strong: "Strong edge",
  moderate: "Moderate edge",
  weak: "Weak edge",
  none: "No edge",
};

const CONFIDENCE_LABELS: Record<MarketEdge["confidence_tier"], string> = {
  higher: "Higher confidence",
  moderate: "Moderate confidence",
  lower: "Lower confidence",
  insufficient_data: "Insufficient data",
};

interface OddsPanelProps {
  matchId: number;
  homeTeamName: string;
  awayTeamName: string;
}

function OddsPanel({ matchId, homeTeamName, awayTeamName }: OddsPanelProps) {
  const [odds, setOdds] = useState<OddsQuote[]>([]);
  const [edges, setEdges] = useState<MarketEdge[]>([]);
  const [oddsLoading, setOddsLoading] = useState(false);
  const [bookmakers, setBookmakers] = useState<string[]>([]);

  const [bookmakerName, setBookmakerName] = useState("");
  const [marketType, setMarketType] = useState<MarketType>("h2h");
  const [selection, setSelection] = useState(homeTeamName);
  const [lineValue, setLineValue] = useState("");
  const [priceDecimal, setPriceDecimal] = useState("");
  const [isClosingLine, setIsClosingLine] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const edgesByQuoteId = useMemo(() => {
    const map = new Map<number, MarketEdge>();
    edges.forEach((e) => map.set(e.odds_quote_id, e));
    return map;
  }, [edges]);

  async function loadOddsAndEdges() {
    setOddsLoading(true);
    try {
      const [oddsData, edgesData] = await Promise.all([fetchOdds(matchId), fetchEdges(matchId)]);
      setOdds(oddsData);
      setEdges(edgesData);
    } finally {
      setOddsLoading(false);
    }
  }

  useEffect(() => {
    fetchBookmakers().then(setBookmakers).catch(() => undefined);
    loadOddsAndEdges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchId]);

  useEffect(() => {
    // reset the selection field to something valid whenever the market type changes
    setSelection(marketType === "total" ? "over" : homeTeamName);
  }, [marketType, homeTeamName]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);

    const price = Number(priceDecimal);
    if (!Number.isFinite(price) || price <= 1.0) {
      setFormError("Price must be a decimal odds value greater than 1.0");
      return;
    }
    const needsLine = marketType !== "h2h";
    const line = lineValue.trim() === "" ? null : Number(lineValue);
    if (needsLine && (line === null || !Number.isFinite(line))) {
      setFormError(`A line value is required for ${MARKET_LABELS[marketType]}`);
      return;
    }

    setSubmitting(true);
    try {
      const created = await createOdds(matchId, {
        bookmaker_name: bookmakerName.trim(),
        market_type: marketType,
        selection,
        line_value: needsLine ? line : null,
        price_decimal: price,
        is_closing_line: isClosingLine,
      });
      setBookmakers((prev) => (prev.includes(created.bookmaker_name) ? prev : [...prev, created.bookmaker_name].sort()));
      setPriceDecimal("");
      setLineValue("");
      setIsClosingLine(false);
      // reload rather than append locally: a new quote can change the de-vig
      // pairing (and therefore the edge) for other quotes on this match too
      await loadOddsAndEdges();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save odds quote");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    await deleteOdds(id);
    await loadOddsAndEdges();
  }

  return (
    <section className="odds-panel">
      <h3>Market odds</h3>

      <form className="odds-form" onSubmit={handleSubmit}>
        <div className="odds-form__row">
          <label>
            Bookmaker
            <input
              list="bookmaker-options"
              value={bookmakerName}
              onChange={(e) => setBookmakerName(e.target.value)}
              placeholder="e.g. Sportsbet"
              required
            />
            <datalist id="bookmaker-options">
              {bookmakers.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>

          <label>
            Market
            <select value={marketType} onChange={(e) => setMarketType(e.target.value as MarketType)}>
              {Object.entries(MARKET_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="odds-form__row">
          <label>
            Selection
            {marketType === "total" ? (
              <select value={selection} onChange={(e) => setSelection(e.target.value)}>
                <option value="over">Over</option>
                <option value="under">Under</option>
              </select>
            ) : (
              <select value={selection} onChange={(e) => setSelection(e.target.value)}>
                <option value={homeTeamName}>{homeTeamName}</option>
                <option value={awayTeamName}>{awayTeamName}</option>
              </select>
            )}
          </label>

          {marketType !== "h2h" && (
            <label>
              Line
              <input
                type="number"
                step="0.5"
                value={lineValue}
                onChange={(e) => setLineValue(e.target.value)}
                placeholder={marketType === "total" ? "e.g. 165.5" : "e.g. -12.5"}
                required
              />
            </label>
          )}

          <label>
            Price (decimal odds)
            <input
              type="number"
              step="0.01"
              min="1.01"
              value={priceDecimal}
              onChange={(e) => setPriceDecimal(e.target.value)}
              placeholder="e.g. 1.85"
              required
            />
          </label>
        </div>

        <label className="odds-form__checkbox">
          <input type="checkbox" checked={isClosingLine} onChange={(e) => setIsClosingLine(e.target.checked)} />
          This is the closing line
        </label>

        {formError && <p className="error-banner">{formError}</p>}

        <button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : "Add odds quote"}
        </button>
      </form>

      <h4>Recorded quotes</h4>
      {oddsLoading && <p className="loading-state">Loading…</p>}
      {!oddsLoading && odds.length === 0 && <p className="hint">No odds recorded for this match yet.</p>}
      {!oddsLoading && odds.length > 0 && (
        <div className="odds-table-scroll">
          <table className="odds-table">
            <thead>
              <tr>
                <th>Bookmaker</th>
                <th>Market</th>
                <th>Selection</th>
                <th>Line</th>
                <th>Price</th>
                <th>Model %</th>
                <th>Fair odds</th>
                <th>Edge</th>
                <th>Confidence</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {odds.map((quote) => {
                const edge = edgesByQuoteId.get(quote.id);
                return (
                  <tr key={quote.id}>
                    <td>{quote.bookmaker_name}</td>
                    <td>{MARKET_LABELS[quote.market_type]}</td>
                    <td>
                      {quote.selection}
                      {quote.is_closing_line && <span className="odds-table__badge">closing</span>}
                    </td>
                    <td>{quote.line_value ?? "—"}</td>
                    <td>${quote.price_decimal.toFixed(2)}</td>
                    {edge ? (
                      <>
                        <td
                          title={edge.overround_removed ? "Overround removed" : "Only one side quoted — overround not removed"}
                        >
                          {(edge.model_probability * 100).toFixed(1)}%
                        </td>
                        <td>${edge.fair_odds.toFixed(2)}</td>
                        <td>
                          <span className={`edge-badge edge-badge--${edge.edge_tier}`}>{EDGE_LABELS[edge.edge_tier]}</span>
                        </td>
                        <td>
                          <span
                            className={`confidence-badge confidence-badge--${edge.confidence_tier}`}
                            title={edge.confidence_reasons.join(" ")}
                          >
                            {CONFIDENCE_LABELS[edge.confidence_tier]}
                          </span>
                        </td>
                      </>
                    ) : (
                      <td colSpan={4} className="hint">
                        No model prediction available
                      </td>
                    )}
                    <td>
                      <button type="button" className="odds-table__delete" onClick={() => handleDelete(quote.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default OddsPanel;
