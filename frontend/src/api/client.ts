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

// Recent completed results for the dashboard — reads directly from
// normalised match data, no prediction/odds record required.
export function fetchRecentResults(limit = 8): Promise<MatchSummary[]> {
  return request(`/api/afl/matches?status=completed&order=desc&limit=${limit}`);
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
  // null when the Elo/Poisson modelling CLIs haven't been run yet — the
  // fixture still shows, just without a model view.
  predictions: MatchPredictions | null;
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

// --- Model evaluation / rigorous backtesting (Stage 1B) ---

export interface EvaluationPeriod {
  warmup_start_year: number;
  warmup_end_year: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  current_season_year: number;
  n_warmup: number;
  n_evaluation: number;
  n_current_season: number;
}

export interface BaselineComparisonRow {
  name: string;
  n: number;
  metrics: Record<string, number>;
}

export interface WinProbEvaluation {
  model_name: string;
  period: EvaluationPeriod;
  evaluation_metrics: Record<string, number>;
  warmup_metrics: Record<string, number>;
  full_history_metrics: Record<string, number>;
  baseline_comparison: BaselineComparisonRow[];
  calibration: CalibrationBucket[];
  calibration_ece: number | null;
  by_season: BacktestSegment[];
}

export interface ScoringEvaluation {
  period: EvaluationPeriod;
  evaluation_metrics: Record<string, number>;
  warmup_metrics: Record<string, number>;
  full_history_metrics: Record<string, number>;
  by_season: BacktestSegment[];
  interval_coverage: Record<string, Record<string, number>>;
}

export interface BacktestSummary {
  id: string;
  model_name: string;
  run_at: string;
  tune_end_year: number;
  evaluation_start_year: number;
  n_evaluation: number;
  headline_metrics: Record<string, number>;
}

export interface BacktestDetail {
  id: string;
  model_name: string;
  config: Record<string, unknown>;
  tune_end_year: number;
  run_at: string;
  win_prob: WinProbEvaluation;
  scoring: ScoringEvaluation | null;
}

export interface DisagreementBucket {
  label: string;
  n: number;
  elo_metrics: Record<string, number>;
  poisson_metrics: Record<string, number>;
  actual_home_win_rate: number | null;
}

export interface SeasonStabilityRow {
  season_year: string;
  n_games: number;
  elo_accuracy: number;
  elo_brier: number;
  elo_log_loss: number;
  poisson_total_mae: number;
  poisson_margin_mae: number;
  home_win_rate: number;
}

export interface ModelComparison {
  n_matches: number;
  overall_elo_metrics: Record<string, number>;
  overall_poisson_metrics: Record<string, number>;
  mean_absolute_disagreement: number;
  disagreement_buckets: DisagreementBucket[];
  season_stability: SeasonStabilityRow[];
}

export function fetchBacktests(): Promise<BacktestSummary[]> {
  return request("/api/backtests");
}

export async function fetchBacktestDetail(id: string): Promise<BacktestDetail | null> {
  try {
    return await request(`/api/backtests/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export async function fetchModelComparison(): Promise<ModelComparison | null> {
  try {
    return await request("/api/backtests/comparison");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

// --- Advanced feature engineering / logistic regression (Stage 1C) ---

export interface BaselineModelRow {
  name: string;
  n: number;
  brier_score: number;
  log_loss: number;
  accuracy: number;
}

export interface AblationResult {
  label: string;
  feature_names: string[];
  n_eval: number;
  brier_score: number;
  log_loss: number;
  brier_vs_elo_alone: number | null;
}

export interface BootstrapResultData {
  point_estimate: number;
  ci_low: number;
  ci_high: number;
  n_resamples: number;
  excludes_zero: boolean;
}

export interface PromotionDecisionData {
  promote: boolean;
  reasons: string[];
}

export interface LogisticDisagreementBucket {
  label: string;
  n: number;
  elo_metrics: Record<string, number>;
  logistic_metrics: Record<string, number>;
  actual_home_win_rate: number | null;
}

export interface LogisticComparisonReportData {
  n_matches: number;
  mean_absolute_disagreement: number;
  disagreement_buckets: LogisticDisagreementBucket[];
}

export interface LogisticVariantReport {
  variant: string;
  feature_names: string[];
  C: number;
  calibration_method: string;
  n_eval: number;
  brier_score: number;
  log_loss: number;
  accuracy: number;
  calibration: CalibrationBucket[];
  calibration_ece: number | null;
  standardized_coefficients: Record<string, number>;
  permutation_importance: Record<string, number>;
  single_feature_ablation: Record<string, number>;
  feature_group_ablation: AblationResult[];
  bootstrap_vs_elo: BootstrapResultData;
  by_season: BacktestSegment[];
  disagreement_vs_elo: LogisticComparisonReportData;
  promotion: PromotionDecisionData;
}

export interface LogisticComparisonOverview {
  n_eval: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  baselines: BaselineModelRow[];
  elo: BaselineModelRow;
  poisson: BaselineModelRow;
  stats_only: LogisticVariantReport;
  stats_plus_elo: LogisticVariantReport;
}

export async function fetchLogisticComparison(): Promise<LogisticComparisonOverview | null> {
  try {
    return await request("/api/backtests/logistic-comparison");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

// --- Gradient boosting + ensemble (Stage 2A) ---

export interface FeatureSetCandidateResult {
  label: string;
  library: string;
  feature_names: string[];
  n_eval: number;
  brier_score: number;
  log_loss: number;
  accuracy: number;
}

export interface BoostingAblationResult {
  label: string;
  feature_names: string[];
  n_eval: number;
  brier_score: number;
  log_loss: number;
  brier_vs_elo_alone: number | null;
}

export interface InteractionFinding {
  feature_a: string;
  feature_b: string;
  label: string;
  mean_abs_interaction: number;
  mean_abs_main_effect_a: number;
  mean_abs_main_effect_b: number;
}

export interface BoostingBestCandidateReport {
  label: string;
  library: string;
  feature_names: string[];
  hyperparameters: Record<string, number>;
  calibration_method: string;
  n_eval: number;
  brier_score: number;
  log_loss: number;
  accuracy: number;
  calibration: CalibrationBucket[];
  calibration_ece: number | null;
  permutation_importance: Record<string, number>;
  shap_importance: Record<string, number> | null;
  feature_group_ablation: BoostingAblationResult[];
  bootstrap_vs_elo: BootstrapResultData;
  by_season: BacktestSegment[];
  disagreement_vs_elo: LogisticComparisonReportData;
  interactions: InteractionFinding[];
  promotion: PromotionDecisionData;
}

export interface EnsembleReport {
  boosting_weight: number;
  elo: BaselineModelRow;
  boosting: BaselineModelRow;
  ensemble: BaselineModelRow;
  bootstrap_ensemble_vs_elo: BootstrapResultData;
  bootstrap_ensemble_vs_boosting: BootstrapResultData;
  use_ensemble: boolean;
}

export interface BoostingComparisonOverview {
  n_eval: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  feature_set_candidates: FeatureSetCandidateResult[];
  best: BoostingBestCandidateReport;
  ensemble: EnsembleReport;
}

export async function fetchBoostingComparison(): Promise<BoostingComparisonOverview | null> {
  try {
    return await request("/api/backtests/boosting-comparison");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

// --- Poisson season-transition revision (Stage 2B) ---

export interface PoissonConfigData {
  rolling_window_games: number;
  min_games_for_reliable_strength: number;
  min_league_games_for_home_split: number;
  max_goals: number;
  max_behinds: number;
  league_window_games: number | null;
}

export interface RoundBandMetrics {
  label: string;
  n: number;
  metrics: Record<string, number>;
}

export interface PoissonVariantReport {
  label: string;
  config: PoissonConfigData;
  period: EvaluationPeriod;
  evaluation_metrics: Record<string, number>;
  warmup_metrics: Record<string, number>;
  full_history_metrics: Record<string, number>;
  by_season: BacktestSegment[];
  early_season_bands: RoundBandMetrics[];
  season_2021_bands: RoundBandMetrics[];
  interval_coverage: Record<string, Record<string, number>>;
}

export interface TuneLeaderboardRow {
  config: PoissonConfigData;
  tune_total_points_mae: number;
}

export interface PoissonRevisionComparison {
  original: PoissonVariantReport;
  revised: PoissonVariantReport;
  tune_leaderboard_top5: TuneLeaderboardRow[];
  common_match_count: number;
  revised_beats_original_2021: boolean;
  revised_worse_than_original_full_history: boolean;
  promotion: PromotionDecisionData;
}

export async function fetchPoissonRevision(): Promise<PoissonRevisionComparison | null> {
  try {
    return await request("/api/backtests/poisson-revision");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}
