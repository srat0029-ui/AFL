const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface DbHealthResponse {
  status: string;
  database: string;
  sport_rows: number;
}

export async function fetchDbHealth(): Promise<DbHealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health/db`);
  if (!response.ok) {
    throw new Error(`Backend responded with ${response.status}`);
  }
  return response.json();
}

export interface Team {
  id: number;
  name: string;
  short_name: string;
  primary_colour: string | null;
  secondary_colour: string | null;
}

export interface Venue {
  id: number;
  name: string;
  city: string | null;
}

export interface MatchSummary {
  id: number;
  season_year: number;
  round_number: number;
  status: string;
  scheduled_start: string;
  home_team: Team;
  away_team: Team;
  venue: Venue | null;
  home_score: number | null;
  away_score: number | null;
}

export type MarketType = "h2h" | "line" | "total";

export interface OddsQuote {
  id: number;
  match_id: number;
  bookmaker_name: string;
  market_type: MarketType;
  selection: string;
  line_value: number | null;
  price_decimal: number;
  recorded_at: string;
  source: string;
  is_closing_line: boolean;
}

export interface OddsQuoteInput {
  bookmaker_name: string;
  market_type: MarketType;
  selection: string;
  line_value?: number | null;
  price_decimal: number;
  is_closing_line?: boolean;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((e: { msg?: string }) => e.msg).join("; ");
    }
  } catch {
    // fall through to generic message below
  }
  return `Request failed with status ${response.status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response), response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

export function fetchMatches(status?: string): Promise<MatchSummary[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/matches${query}`);
}

export function fetchMatch(matchId: number): Promise<MatchSummary> {
  return request(`/api/matches/${matchId}`);
}

export function fetchOdds(matchId: number): Promise<OddsQuote[]> {
  return request(`/api/matches/${matchId}/odds`);
}

export function createOdds(matchId: number, input: OddsQuoteInput): Promise<OddsQuote> {
  return request(`/api/matches/${matchId}/odds`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteOdds(oddsId: number): Promise<void> {
  return request(`/api/odds/${oddsId}`, { method: "DELETE" });
}

export function fetchBookmakers(): Promise<string[]> {
  return request("/api/bookmakers");
}

export type EdgeTier = "none" | "weak" | "moderate" | "strong";
export type ConfidenceTier = "insufficient_data" | "lower" | "moderate" | "higher";

export interface MarketEdge {
  match_id: number;
  odds_quote_id: number;
  market_type: MarketType;
  selection: string;
  line_value: number | null;
  bookmaker_name: string;
  price_decimal: number;
  model_probability: number;
  secondary_model_probability: number | null;
  market_implied_probability: number;
  fair_market_probability: number;
  overround_removed: boolean;
  fair_odds: number;
  model_edge: number;
  expected_value: number;
  edge_tier: EdgeTier;
  confidence_tier: ConfidenceTier;
  confidence_reasons: string[];
}

export async function fetchEdges(matchId: number): Promise<MarketEdge[]> {
  try {
    return await request(`/api/matches/${matchId}/edges`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      // the modelling CLIs haven't been run yet — treat as "no edges available" rather than an error banner
      return [];
    }
    throw err;
  }
}

export interface MatchPredictions {
  match_id: number;
  elo_home_win_probability: number;
  poisson_home_win_probability: number;
  poisson_draw_probability: number;
  poisson_away_win_probability: number;
  poisson_home_expected_score: number;
  poisson_away_expected_score: number;
  poisson_expected_total_points: number;
  poisson_expected_margin: number;
}

export async function fetchPredictions(matchId: number): Promise<MatchPredictions | null> {
  try {
    return await request(`/api/matches/${matchId}/predictions`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export interface DashboardEntry {
  match: MatchSummary;
  predictions: MatchPredictions;
  best_edge: MarketEdge | null;
}

export async function fetchDashboard(): Promise<DashboardEntry[]> {
  try {
    return await request("/api/dashboard");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return [];
    }
    throw err;
  }
}

export interface BacktestSegment {
  label: string;
  n: number;
  metrics: Record<string, number>;
}

export interface CalibrationBucket {
  bucket: string;
  n: number;
  avg_predicted: number | null;
  actual_rate: number | null;
}

export interface WinProbReport {
  model_name: string;
  overall: BacktestSegment;
  by_season: BacktestSegment[];
  by_team: BacktestSegment[];
  by_conviction: BacktestSegment[];
  calibration: CalibrationBucket[];
}

export interface ScoringReport {
  overall: BacktestSegment;
  by_season: BacktestSegment[];
}

export interface TrackedSelection {
  match_id: number;
  market_type: MarketType;
  selection: string;
  line_value: number | null;
  bookmaker_name: string;
  price_decimal: number;
  is_closing_line: boolean;
  model_probability: number;
  won: boolean | null;
  pnl_units: number;
}

export interface LoggedOddsReport {
  n_total: number;
  n_resolved: number;
  n_void: number;
  win_rate: number | null;
  roi_pct: number | null;
  yield_pct: number | null;
  total_pnl_units: number;
  brier_score: number | null;
  log_loss: number | null;
  selections: TrackedSelection[];
}

export interface BacktestOverview {
  elo: WinProbReport;
  poisson_win: WinProbReport;
  poisson_scoring: ScoringReport;
  logged_odds: LoggedOddsReport;
}

export async function fetchBacktest(): Promise<BacktestOverview | null> {
  try {
    return await request("/api/backtest");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}
