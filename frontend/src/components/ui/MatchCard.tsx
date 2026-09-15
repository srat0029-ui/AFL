import { Link } from "react-router-dom";
import type { MatchSummary } from "../../api/client";
import { formatCompactDateTime } from "../../lib/datetime";
import "./MatchCard.css";

/** The one visual object a match becomes everywhere in the product: a
 * clean "Team A vs Team B" card with date/venue/round and clear next
 * actions — never a dense stat table as the first thing shown. */
function MatchCard({ match }: { match: MatchSummary }) {
  const isCompleted = match.status === "completed";
  const isLive = match.status === "in_progress";
  return (
    <div className="match-card">
      <div className="match-card__meta">
        <span>Round {match.round_number}</span>
        {isLive && <span className="chip chip--danger">Live</span>}
        {isCompleted && <span className="chip chip--neutral">Final</span>}
      </div>
      <div className="match-card__matchup">
        <span className="match-card__team">{match.home_team.short_name}</span>
        <span className="match-card__score">
          {isCompleted || isLive ? (
            <>
              {match.home_score ?? "–"} <span className="match-card__vs">–</span> {match.away_score ?? "–"}
            </>
          ) : (
            <span className="match-card__vs">vs</span>
          )}
        </span>
        <span className="match-card__team">{match.away_team.short_name}</span>
      </div>
      <div className="match-card__names">
        <span>{match.home_team.name}</span>
        <span>{match.away_team.name}</span>
      </div>
      <div className="match-card__details">
        <span>{formatCompactDateTime(match.scheduled_start)}</span>
        {match.venue && <span>{match.venue.name}</span>}
      </div>
      <div className="match-card__actions">
        <Link to={`/matches/${match.id}`} className="match-card__action match-card__action--primary">
          View match
        </Link>
        <Link to={`/matches/${match.id}#players`} className="match-card__action">
          Players
        </Link>
        <Link to={`/matches/${match.id}#markets`} className="match-card__action">
          Markets
        </Link>
      </div>
    </div>
  );
}

export default MatchCard;
