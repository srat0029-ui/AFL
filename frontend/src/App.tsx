import { useEffect, useLayoutEffect, useRef, useState, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import "./App.css";
import B2BDemoPage from "./pages/B2BDemoPage";
import BacktestPage from "./pages/BacktestPage";
import HomePage from "./pages/HomePage";
import MatchDetailPage from "./pages/MatchDetailPage";
import MatchesPage from "./pages/MatchesPage";
import MultisPage from "./pages/MultisPage";
import MarketMonitorPage from "./pages/MarketMonitorPage";
import MarketMovementExplorerPage from "./pages/MarketMovementExplorerPage";
import ModelRegistryPage from "./pages/ModelRegistryPage";
import PlacedBetsPage from "./pages/PlacedBetsPage";
import PlayerInsightsPage from "./pages/PlayerInsightsPage";
import PlayerProfilePage from "./pages/PlayerProfilePage";
import PlayerResearchPage from "./pages/PlayerResearchPage";
import PlayersHomePage from "./pages/PlayersHomePage";
import LiveStatusPage from "./pages/LiveStatusPage";
import PropInsightsPage from "./pages/PropInsightsPage";
import ProspectiveEvidenceCenterPage from "./pages/ProspectiveEvidenceCenterPage";
import RealMarketTrackingPage from "./pages/RealMarketTrackingPage";
import RoundContextDashboardPage from "./pages/RoundContextDashboardPage";
import StatusPage from "./pages/StatusPage";
import TeamSelectionPage from "./pages/TeamSelectionPage";
import TradingMonitorPage from "./pages/TradingMonitorPage";
import WeeklyReviewPage from "./pages/WeeklyReviewPage";

/** Product UX Overhaul: information architecture rebuilt around what a
 * new visitor actually wants to do (find a match, find a player, see
 * market/insight tools) rather than around the app's internal module
 * names. "Insights" and "Advanced" are the only two grouped dropdowns —
 * everything else is a single obvious top-level destination. Advanced
 * groups every model-evaluation/operational/B2B surface behind one
 * clearly-labelled door so a casual visitor is never confronted with it,
 * while it all remains one click away for anyone who wants it. */
const PRIMARY_LINKS: { to: string; label: string; extraActivePaths?: string[] }[] = [
  { to: "/", label: "Home" },
  { to: "/matches", label: "Matches", extraActivePaths: ["/matches"] },
  { to: "/players", label: "Players", extraActivePaths: ["/players", "/player-research", "/player-insights"] },
  { to: "/placed-bets", label: "My Bets" },
];

const NAV_GROUPS: { label: string; links: { to: string; label: string }[] }[] = [
  {
    label: "Insights",
    links: [
      { to: "/prop-insights", label: "Player Props" },
      { to: "/weekly-review", label: "Weekly Picks" },
      { to: "/market-movement", label: "Market Movement" },
      { to: "/real-market-tracking", label: "Real Market Tracking" },
    ],
  },
  {
    label: "Advanced",
    links: [
      { to: "/model-registry", label: "Model Evaluation" },
      { to: "/prospective-evidence", label: "Live Model Results" },
      { to: "/backtest", label: "Backtesting" },
      { to: "/trading-monitor", label: "Trading Monitor" },
      { to: "/market-monitor", label: "Market Monitor (QA)" },
      { to: "/b2b-demo", label: "B2B Demo" },
      { to: "/round-context", label: "Round Readiness" },
      { to: "/team-selection", label: "Lineup Entry" },
      { to: "/live-status", label: "Live Status" },
      { to: "/status", label: "System Health" },
    ],
  },
];

function isPathActive(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

function isGroupActive(group: { links: { to: string }[] }, pathname: string): boolean {
  return group.links.some((l) => isPathActive(pathname, l.to));
}

function NavGroup({
  label,
  links,
  isOpen,
  isActive,
  onOpenChange,
}: {
  label: string;
  links: { to: string; label: string }[];
  isOpen: boolean;
  isActive: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  // Browsers fire a native "toggle" event even for a programmatic `.open =`
  // assignment, not just user clicks - without this guard, closing a
  // sibling group below re-enters onOpenChange(false) and immediately
  // undoes whichever group the user just opened.
  const ignoreNextToggle = useRef(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (ref.current && ref.current.open !== isOpen) {
      ignoreNextToggle.current = true;
      ref.current.open = isOpen;
    }
  }, [isOpen]);

  // The menu is portalled to <body> and positioned in fixed coordinates
  // (see render below) rather than living inside .app-nav — that container
  // has overflow-x: auto for narrow-viewport scrolling, and per the CSS
  // overflow spec that silently forces overflow-y: auto too, which was
  // clipping/scroll-trapping the dropdown inside the thin nav strip instead
  // of letting it float below it.
  useLayoutEffect(() => {
    if (isOpen && ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setMenuPos({ top: rect.bottom + 5, left: rect.left });
    } else {
      setMenuPos(null);
    }
  }, [isOpen]);

  function handleToggle(e: SyntheticEvent<HTMLDetailsElement>) {
    if (ignoreNextToggle.current) {
      ignoreNextToggle.current = false;
      return;
    }
    onOpenChange(e.currentTarget.open);
  }

  return (
    <details ref={ref} className={isActive ? "app-nav__group app-nav__group--active" : "app-nav__group"} onToggle={handleToggle}>
      <summary>{label}</summary>
      {isOpen &&
        menuPos &&
        createPortal(
          <div className="app-nav__group-menu" style={{ top: menuPos.top, left: menuPos.left }}>
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}
                onClick={() => onOpenChange(false)}
              >
                {l.label}
              </NavLink>
            ))}
          </div>,
          document.body
        )}
    </details>
  );
}

function App() {
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const location = useLocation();

  useEffect(() => {
    setOpenGroup(null);
    setMobileOpen(false);
  }, [location.pathname]);

  // Close on a click outside the nav bar. The open group's menu is portalled
  // to <body> (see NavGroup) so it's no longer a DOM descendant of navRef -
  // explicitly allow clicks landing inside it too.
  useEffect(() => {
    function handlePointerDown(e: PointerEvent) {
      const target = e.target as Element;
      if (navRef.current?.contains(target)) return;
      if (target.closest?.(".app-nav__group-menu")) return;
      setOpenGroup(null);
    }
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    function handleScroll() {
      setOpenGroup(null);
    }
    nav.addEventListener("scroll", handleScroll);
    return () => nav.removeEventListener("scroll", handleScroll);
  }, []);

  function linkClass(link: { to: string; extraActivePaths?: string[] }) {
    const active = isPathActive(location.pathname, link.to) || (link.extraActivePaths ?? []).some((p) => isPathActive(location.pathname, p));
    return active ? "app-nav__link app-nav__link--active" : "app-nav__link";
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__inner">
          <NavLink to="/" className="app-nav__brand">
            <span className="app-nav__brand-mark">AFL</span>
            Research &amp; Markets
          </NavLink>
          <nav className="app-nav" ref={navRef}>
            {PRIMARY_LINKS.filter((l) => l.to !== "/placed-bets").map((l) => (
              <NavLink key={l.to} to={l.to} end={l.to === "/"} className={() => linkClass(l)}>
                {l.label}
              </NavLink>
            ))}
            {NAV_GROUPS.map((g) => (
              <NavGroup
                key={g.label}
                label={g.label}
                links={g.links}
                isOpen={openGroup === g.label}
                isActive={isGroupActive(g, location.pathname)}
                onOpenChange={(open) => setOpenGroup(open ? g.label : null)}
              />
            ))}
            <span className="app-nav__spacer" />
            <NavLink to="/placed-bets" className={() => linkClass({ to: "/placed-bets" })}>
              My Bets
            </NavLink>
            <NavLink to="/multis" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--pinned app-nav__link--active" : "app-nav__link app-nav__link--pinned")}>
              Multis
            </NavLink>
          </nav>
          <button
            type="button"
            className="app-nav__mobile-toggle"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((v) => !v)}
          >
            {mobileOpen ? "✕" : "☰"}
          </button>
        </div>
        <div className={mobileOpen ? "app-nav__mobile-panel app-nav__mobile-panel--open" : "app-nav__mobile-panel"}>
          {PRIMARY_LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === "/"} className={() => linkClass(l)}>
              {l.label}
            </NavLink>
          ))}
          <NavLink to="/multis" className={({ isActive }) => (isActive ? "app-nav__link app-nav__link--active" : "app-nav__link")}>
            Multis
          </NavLink>
          {NAV_GROUPS.map((g) => (
            <div key={g.label} className="app-nav__mobile-panel-group">
              <div className="app-nav__mobile-group-label">{g.label}</div>
              {g.links.map((l) => (
                <NavLink key={l.to} to={l.to} className={() => linkClass(l)}>
                  {l.label}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/matches" element={<MatchesPage />} />
          <Route path="/players" element={<PlayersHomePage />} />
          <Route path="/weekly-review" element={<WeeklyReviewPage />} />
          <Route path="/round-context" element={<RoundContextDashboardPage />} />
          <Route path="/team-selection" element={<TeamSelectionPage />} />
          <Route path="/matches/:matchId" element={<MatchDetailPage />} />
          <Route path="/players/:playerId" element={<PlayerProfilePage />} />
          <Route path="/player-insights" element={<PlayerInsightsPage />} />
          <Route path="/player-research" element={<PlayerResearchPage />} />
          <Route path="/prop-insights" element={<PropInsightsPage />} />
          <Route path="/multis" element={<MultisPage />} />
          <Route path="/placed-bets" element={<PlacedBetsPage />} />
          <Route path="/model-registry" element={<ModelRegistryPage />} />
          <Route path="/prospective-evidence" element={<ProspectiveEvidenceCenterPage />} />
          <Route path="/b2b-demo" element={<B2BDemoPage />} />
          <Route path="/trading-monitor" element={<TradingMonitorPage />} />
          <Route path="/market-monitor" element={<MarketMonitorPage />} />
          <Route path="/market-movement" element={<MarketMovementExplorerPage />} />
          <Route path="/real-market-tracking" element={<RealMarketTrackingPage />} />
          <Route path="/live-status" element={<LiveStatusPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/status" element={<StatusPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
