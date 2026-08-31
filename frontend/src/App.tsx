import { useEffect, useLayoutEffect, useRef, useState, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
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
import TradingMonitorPage from "./pages/TradingMonitorPage";
import WeeklyReviewPage from "./pages/WeeklyReviewPage";

// Section 4: grouped nav, Multis pinned outside every group as the primary
// during-finals destination. Groups map to the brief's suggested structure,
// extended just enough to place every existing route somewhere sensible.
// Routes that don't have their own nav link (drill-in/detail pages reached
// by clicking through from elsewhere) but should still light up the group
// they conceptually belong to, so a group's active state doesn't go dark
// just because you're one level deeper than its listed links.
const EXTRA_GROUP_ROUTES: Record<string, string[]> = {
  Analysis: ["/players", "/matches"],
};

function isPathActive(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

function isGroupActive(group: { label: string; links: { to: string }[] }, pathname: string): boolean {
  if (group.links.some((l) => isPathActive(pathname, l.to))) return true;
  return (EXTRA_GROUP_ROUTES[group.label] ?? []).some((prefix) => isPathActive(pathname, prefix));
}

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
      { to: "/trading-monitor", label: "Trading Monitor" },
      { to: "/market-monitor", label: "Market Monitor" },
      { to: "/model-registry", label: "Model Evaluation" },
      { to: "/b2b-demo", label: "B2B Demo" },
      { to: "/real-market-tracking", label: "Real Market Tracking" },
      { to: "/backtest", label: "Backtesting" },
    ],
  },
];

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

  // Keep the native <details> element in sync with lifted state so only one
  // group can be open at a time, and so the group closes on navigation or
  // an outside click instead of staying open indefinitely (native <details>
  // has no such behaviour on its own, which was the source of the "top bar
  // doesn't work properly" complaint).
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
  const navRef = useRef<HTMLElement>(null);
  const location = useLocation();

  // Close any open dropdown whenever the route changes (covers link clicks,
  // back/forward navigation, and programmatic navigation alike).
  useEffect(() => {
    setOpenGroup(null);
  }, [location.pathname]);

  // Close on a click outside the nav bar. The open group's menu is portalled
  // to <body> (see NavGroup) so it's no longer a DOM descendant of navRef -
  // explicitly allow clicks landing inside it too, or every click on a link
  // in the menu would close it out from under itself before the link's own
  // onClick/navigation had a chance to run.
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

  // The portalled menu's position is computed once on open from the
  // trigger's bounding rect - close it on nav scroll so it can't be left
  // hanging in a stale position if the (horizontally-scrollable) nav bar
  // moves out from under it.
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    function handleScroll() {
      setOpenGroup(null);
    }
    nav.addEventListener("scroll", handleScroll);
    return () => nav.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <>
      <nav className="app-nav" ref={navRef}>
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
        <Route path="/trading-monitor" element={<TradingMonitorPage />} />
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
