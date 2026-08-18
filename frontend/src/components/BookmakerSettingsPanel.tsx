import { useEffect, useState } from "react";
import { fetchBookmakerEligibility, updateBookmakerEligibility, type BookmakerEligibility, type BookmakerSetting } from "../api/client";
import "./BookmakerSettingsPanel.css";

const ELIGIBILITY_OPTIONS: { value: BookmakerEligibility; label: string }[] = [
  { value: "included", label: "Included — used for best-price calculations" },
  { value: "informational_only", label: "Informational only — shown, never headlined as best price" },
  { value: "excluded", label: "Excluded — hidden from price comparisons" },
];

/** Market Integrity + Final Weekly Picks stage, Sections 5 & 13: "Bookmakers
 * I use" settings panel. Never hardcoded — every bookmaker this app has
 * ever observed (manual entry or an automated provider) appears here, with
 * its automatically-detected exchange status and a user-editable
 * eligibility. Best Opportunities / Final Shortlist "best price" only ever
 * uses "included" bookmakers; "informational_only" and "excluded" prices
 * remain fully visible in the comparison drawer, just never headlined. */
function BookmakerSettingsPanel() {
  const [bookmakers, setBookmakers] = useState<BookmakerSetting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchBookmakerEligibility()
      .then(setBookmakers)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load bookmakers"))
      .finally(() => setLoading(false));
  }, []);

  function handleChange(bookmaker: BookmakerSetting, eligibility: BookmakerEligibility) {
    setSavingId(bookmaker.id);
    updateBookmakerEligibility(bookmaker.id, eligibility)
      .then((updated) => setBookmakers((prev) => prev.map((b) => (b.id === updated.id ? updated : b))))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to update eligibility"))
      .finally(() => setSavingId(null));
  }

  if (loading) return <p className="loading-state">Loading…</p>;

  return (
    <div className="bookmaker-settings">
      <p className="hint bookmaker-settings__intro">
        Choose which bookmakers count toward "best price" across this app. Exchanges (like Betfair) default to
        "Informational only" — their back price is set by other bettors, not a fixed-odds book, and can legitimately
        diverge from sportsbook consensus. All-bookmaker data always stays visible in the comparison drawer regardless
        of this setting.
      </p>
      {error && <div className="prop-insights-page__error">{error}</div>}
      <div className="bookmaker-settings__list">
        {bookmakers.map((b) => (
          <div key={b.id} className="bookmaker-settings__row">
            <div className="bookmaker-settings__name">
              {b.name}
              {b.is_exchange && <span className="bookmaker-settings__exchange-flag">exchange</span>}
            </div>
            <select
              value={b.eligibility}
              disabled={savingId === b.id}
              onChange={(e) => handleChange(b, e.target.value as BookmakerEligibility)}
            >
              {ELIGIBILITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}

export default BookmakerSettingsPanel;
