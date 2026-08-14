import { useEffect, useMemo, useState } from "react";
import "./OddsEntryPage.css";
import {
  createOdds,
  deleteOdds,
  fetchBookmakers,
  fetchMatches,
  fetchOdds,
  type MarketType,
  type MatchSummary,
  type OddsQuote,
} from "../api/client";

const MARKET_LABELS: Record<MarketType, string> = {
  h2h: "Match winner (H2H)",
  line: "Line / handicap",
  total: "Total points",
};

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function OddsEntryPage() {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [matchesError, setMatchesError] = useState<string | null>(null);
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);

  const [odds, setOdds] = useState<OddsQuote[]>([]);
  const [oddsLoading, setOddsLoading] = useState(false);
  const [bookmakers, setBookmakers] = useState<string[]>([]);

  const [bookmakerName, setBookmakerName] = useState("");
  const [marketType, setMarketType] = useState<MarketType>("h2h");
  const [selection, setSelection] = useState("");
  const [lineValue, setLineValue] = useState("");
  const [priceDecimal, setPriceDecimal] = useState("");
  const [isClosingLine, setIsClosingLine] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetchMatches("scheduled")
      .then((data) => {
        setMatches(data);
        if (data.length > 0) setSelectedMatchId(data[0].id);
      })
      .catch((err) => setMatchesError(err instanceof Error ? err.message : "Failed to load matches"));
    fetchBookmakers().then(setBookmakers).catch(() => undefined);
  }, []);

  const selectedMatch = useMemo(
    () => matches.find((m) => m.id === selectedMatchId) ?? null,
    [matches, selectedMatchId],
  );

  useEffect(() => {
    if (selectedMatchId === null) return;
    setOddsLoading(true);
    fetchOdds(selectedMatchId)
      .then(setOdds)
      .finally(() => setOddsLoading(false));
  }, [selectedMatchId]);

  useEffect(() => {
    // reset the selection field to something valid whenever the market type changes
    if (marketType === "total") {
      setSelection("over");
    } else {
      setSelection(selectedMatch?.home_team.name ?? "");
    }
  }, [marketType, selectedMatch]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (selectedMatchId === null) return;
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
      const created = await createOdds(selectedMatchId, {
        bookmaker_name: bookmakerName.trim(),
        market_type: marketType,
        selection,
        line_value: needsLine ? line : null,
        price_decimal: price,
        is_closing_line: isClosingLine,
      });
      setOdds((prev) => [created, ...prev]);
      setBookmakers((prev) => (prev.includes(created.bookmaker_name) ? prev : [...prev, created.bookmaker_name].sort()));
      setPriceDecimal("");
      setLineValue("");
      setIsClosingLine(false);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save odds quote");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    await deleteOdds(id);
    setOdds((prev) => prev.filter((q) => q.id !== id));
  }

  return (
    <main className="odds-page">
      <h1>Odds entry</h1>
      <p className="subtitle">Log bookmaker prices for upcoming fixtures.</p>

      {matchesError && <div className="odds-page__error">{matchesError}</div>}

      {matches.length === 0 && !matchesError && (
        <p className="hint">No upcoming fixtures found. Run the ingestion CLI to pull them in.</p>
      )}

      {matches.length > 0 && (
        <div className="odds-page__layout">
          <aside className="match-list">
            {matches.map((match) => (
              <button
                key={match.id}
                className={`match-list__item ${match.id === selectedMatchId ? "match-list__item--active" : ""}`}
                onClick={() => setSelectedMatchId(match.id)}
              >
                <span className="match-list__teams">
                  {match.home_team.short_name} vs {match.away_team.short_name}
                </span>
                <span className="match-list__meta">{formatKickoff(match.scheduled_start)}</span>
              </button>
            ))}
          </aside>

          <section className="odds-panel">
            {selectedMatch && (
              <>
                <h2>
                  {selectedMatch.home_team.name} vs {selectedMatch.away_team.name}
                </h2>
                <p className="hint">
                  {formatKickoff(selectedMatch.scheduled_start)}
                  {selectedMatch.venue ? ` · ${selectedMatch.venue.name}` : ""}
                </p>

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
                          <option value={selectedMatch.home_team.name}>{selectedMatch.home_team.name}</option>
                          <option value={selectedMatch.away_team.name}>{selectedMatch.away_team.name}</option>
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
                    <input
                      type="checkbox"
                      checked={isClosingLine}
                      onChange={(e) => setIsClosingLine(e.target.checked)}
                    />
                    This is the closing line
                  </label>

                  {formError && <p className="odds-page__error">{formError}</p>}

                  <button type="submit" disabled={submitting}>
                    {submitting ? "Saving…" : "Add odds quote"}
                  </button>
                </form>

                <h3>Recorded quotes</h3>
                {oddsLoading && <p className="hint">Loading…</p>}
                {!oddsLoading && odds.length === 0 && <p className="hint">No odds recorded for this match yet.</p>}
                {!oddsLoading && odds.length > 0 && (
                  <table className="odds-table">
                    <thead>
                      <tr>
                        <th>Bookmaker</th>
                        <th>Market</th>
                        <th>Selection</th>
                        <th>Line</th>
                        <th>Price</th>
                        <th>Recorded</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {odds.map((quote) => (
                        <tr key={quote.id}>
                          <td>{quote.bookmaker_name}</td>
                          <td>{MARKET_LABELS[quote.market_type]}</td>
                          <td>
                            {quote.selection}
                            {quote.is_closing_line && <span className="odds-table__badge">closing</span>}
                          </td>
                          <td>{quote.line_value ?? "—"}</td>
                          <td>${quote.price_decimal.toFixed(2)}</td>
                          <td>{new Date(quote.recorded_at).toLocaleDateString()}</td>
                          <td>
                            <button type="button" className="odds-table__delete" onClick={() => handleDelete(quote.id)}>
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

export default OddsEntryPage;
