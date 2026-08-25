import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import B2BDemoPage from "./pages/B2BDemoPage";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import MatchDetailPage from "./pages/MatchDetailPage";
import MultisPage from "./pages/MultisPage";
import MarketMonitorPage from "./pages/MarketMonitorPage";
import ModelRegistryPage from "./pages/ModelRegistryPage";
import PlacedBetsPage from "./pages/PlacedBetsPage";
import PlayerInsightsPage from "./pages/PlayerInsightsPage";
import PlayerProfilePage from "./pages/PlayerProfilePage";
import LiveStatusPage from "./pages/LiveStatusPage";
import PropInsightsPage from "./pages/PropInsightsPage";
import RealMarketTrackingPage from "./pages/RealMarketTrackingPage";
import RoundContextDashboardPage from "./pages/RoundContextDashboardPage";
import StatusPage from "./pages/StatusPage";
import TeamSelectionPage from "./pages/TeamSelectionPage";
import WeeklyReviewPage from "./pages/WeeklyReviewPage";

// Section 4: grouped nav, Multis pinned outside every group as the primary
// during-finals destination. Groups map to the brief's suggested structure,
// extended just enough to place every existing route somewhere sensible.
const NAV_GROUPS: { label: string; links: { to: string; label: string }[] }[] = [
  {
    label: "Analysis",
    links: [
      { to: "/round-context", label: "Matches" },
      { to: "/player-insights", label: "Player Insights" },
      { to: "/prop-insights", label: "Prop Insights" },
      { to: "/weekly-review", label: "Weekly Review" },
      { to: "/team-selection", label: "Team Selection" },
    ],
  },
  {
    label: "Tracking",
    links: [
      { to: "/placed-bets", label: "Placed Bets" },
      { to: "/live-status", label: "Live Status" },
    ],
  },
  {
    label: "Trading / B2B",
    links: [
      { to: "/market-monitor", label: "Market Monitor" },
      { to: "/model-registry", label: "Model Evaluation" },
      { to: "/b2b-demo", label: "B2B Demo" },
      { to: "/real-market-tracking", label: "Real Market Tracking" },
      { to: "/backtest", label: "Backtesting" },
    ],
  },
];

function NavGroup({ label, links }: { label: string; links: { to: string; label: string }[] }) {
  return (
    <details className="app-nav__group">
      <summary>{label}</summary>
      <div className="app-nav__group-menu">
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
            {l.label}
          </NavLink>
        ))}
      </div>
    </details>
  );
}

function App() {
  return (
    <>
      <nav className="app-nav">
        <span className="app-nav__brand">AFL Analytics</span>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Dashboard
        </NavLink>
        <NavLink
          to="/multis"
          className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--pinned app-nav__link--active" : "app-nav__link app-nav__link--pinned")}
        >
          Finals / Multis
        </NavLink>
        {NAV_GROUPS.map((g) => (
          <NavGroup key={g.label} label={g.label} links={g.links} />
        ))}
        <span className="app-nav__spacer" />
        <NavLink to="/status" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Status
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/weekly-review" element={<WeeklyReviewPage />} />
        <Route path="/round-context" element={<RoundContextDashboardPage />} />
        <Route path="/team-selection" element={<TeamSelectionPage />} />
        <Route path="/matches/:matchId" element={<MatchDetailPage />} />
        <Route path="/players/:playerId" element={<PlayerProfilePage />} />
        <Route path="/player-insights" element={<PlayerInsightsPage />} />
        <Route path="/prop-insights" element={<PropInsightsPage />} />
        <Route path="/multis" element={<MultisPage />} />
        <Route path="/placed-bets" element={<PlacedBetsPage />} />
        <Route path="/model-registry" element={<ModelRegistryPage />} />
        <Route path="/b2b-demo" element={<B2BDemoPage />} />
        <Route path="/market-monitor" element={<MarketMonitorPage />} />
        <Route path="/real-market-tracking" element={<RealMarketTrackingPage />} />
        <Route path="/live-status" element={<LiveStatusPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/status" element={<StatusPage />} />
      </Routes>
    </>
  );
}

export default App;
