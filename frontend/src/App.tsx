import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import OddsEntryPage from "./pages/OddsEntryPage";
import StatusPage from "./pages/StatusPage";

function App() {
  return (
    <>
      <nav className="app-nav">
        <span className="app-nav__brand">AFL Analytics</span>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Odds Entry
        </NavLink>
        <NavLink to="/status" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
          Status
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<OddsEntryPage />} />
        <Route path="/status" element={<StatusPage />} />
      </Routes>
    </>
  );
}

export default App;
