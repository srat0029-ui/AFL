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

class ApiError extends Error {}

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
    throw new ApiError(await parseErrorDetail(response));
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
