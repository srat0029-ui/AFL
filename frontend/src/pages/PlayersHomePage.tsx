import { Link } from "react-router-dom";
import "./PlayersHomePage.css";
import PageHeader from "../components/ui/PageHeader";
import PlayerSearch from "../components/ui/PlayerSearch";

function PlayersHomePage() {
  return (
    <main className="players-home-page">
      <PageHeader
        eyebrow="Players"
        title="Player research"
        description="Search for any player to see their projections, recent form, and market movement — or browse the tools below for a wider view."
      />

      <PlayerSearch placeholder="Search by player name, e.g. Nick Daicos" />

      <div className="players-home-page__grid">
        <Link to="/player-insights" className="card players-home-page__card">
          <span className="players-home-page__card-title">League-wide projections</span>
          <span className="hint">
            See every player's disposal and goal projections for the round at once, filterable by team, confidence
            and threshold — good for spotting standout plays across the whole league.
          </span>
        </Link>
        <Link to="/player-research" className="card players-home-page__card">
          <span className="players-home-page__card-title">Compare teammates &amp; opponents</span>
          <span className="hint">
            Pick a player and see how their numbers actually change with a specific teammate on the field, or against
            a specific opponent — with sample sizes and confidence shown honestly, never a raw effect size alone.
          </span>
        </Link>
      </div>
    </main>
  );
}

export default PlayersHomePage;
