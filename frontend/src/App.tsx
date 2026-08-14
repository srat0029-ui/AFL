import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import BacktestPage from "./pages/BacktestPage";
import DashboardPage from "./pages/DashboardPage";
import MatchDetailPage from "./pages/MatchDetailPage";
import StatusPage from "./pages/StatusPage";

function App() {
  return (
    <>
      <nav className="app-nav">
        <span className="app-nav__brand">AFL Analytics</span>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Dashboard
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
        <Route path="/matches/:matchId" element={<MatchDetailPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/status" element={<StatusPage />} />
      </Routes>
    </>
  );
}

export default App;
