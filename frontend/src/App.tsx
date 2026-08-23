import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import MatchDetailPage from "./pages/MatchDetailPage";
import MultisPage from "./pages/MultisPage";
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

function App() {
  return (
    <>
      <nav className="app-nav">
        <span className="app-nav__brand">AFL Analytics</span>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Dashboard
        </NavLink>
        <NavLink to="/weekly-review" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Weekly Review
        </NavLink>
        <NavLink to="/round-context" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Round Context
        </NavLink>
        <NavLink to="/team-selection" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Team Selection
        </NavLink>
        <NavLink to="/player-insights" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Player Insights
        </NavLink>
        <NavLink to="/prop-insights" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Prop Insights
        </NavLink>
        <NavLink to="/multis" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Multis
        </NavLink>
        <NavLink to="/placed-bets" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Placed Bets
        </NavLink>
        <NavLink to="/real-market-tracking" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Real Market Tracking
        </NavLink>
        <NavLink to="/live-status" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Live Status
        </NavLink>
        <NavLink to="/backtest" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Backtesting
        </NavLink>
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
        <Route path="/real-market-tracking" element={<RealMarketTrackingPage />} />
        <Route path="/live-status" element={<LiveStatusPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/status" element={<StatusPage />} />
      </Routes>
    </>
  );
}

export default App;
