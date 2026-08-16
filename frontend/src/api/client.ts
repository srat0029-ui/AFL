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

// --- Player data foundation (Stage 3) ---

export interface PlayerSummary {
  id: number;
  display_name: string;
  current_team: Team | null;
  is_active: boolean | null;
  source: string;
  source_player_id: string;
}

export interface PlayerList {
  players: PlayerSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PlayerGameStat {
  match_id: number;
  player_id: number;
  player_display_name: string;
  season_year: number;
  round_number: number;
  scheduled_start: string;
  team: Team;
  opponent_team: Team | null;
  jumper_number: number | null;
  subbed_on: boolean;
  subbed_off: boolean;
  kicks: number | null;
  marks: number | null;
  handballs: number | null;
  disposals: number | null;
  goals: number | null;
  behinds: number | null;
  hitouts: number | null;
  tackles: number | null;
  rebound_50s: number | null;
  inside_50s: number | null;
  clearances: number | null;
  clangers: number | null;
  frees_for: number | null;
  frees_against: number | null;
  brownlow_votes: number | null;
  contested_possessions: number | null;
  uncontested_possessions: number | null;
  contested_marks: number | null;
  marks_inside_50: number | null;
  one_percenters: number | null;
  bounces: number | null;
  goal_assists: number | null;
  time_on_ground_pct: number | null;
  fantasy_points: number | null;
}

export interface PlayerGames {
  player: PlayerSummary;
  games: PlayerGameStat[];
  total: number;
  limit: number;
  offset: number;
}

export interface SeasonAverage {
  season_year: number;
  games_played: number;
  averages: Record<string, number>;
}

export interface PlayerForm {
  player: PlayerSummary;
  recent_games: PlayerGameStat[];
  season_averages: SeasonAverage[];
}

export interface MatchPlayers {
  match_id: number;
  home_team_players: PlayerGameStat[];
  away_team_players: PlayerGameStat[];
}

export function fetchTeams(): Promise<Team[]> {
  return request("/api/afl/teams");
}

export function fetchPlayers(params: {
  teamId?: number;
  season?: number;
  isActive?: boolean;
  name?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<PlayerList> {
  const query = new URLSearchParams();
  if (params.teamId !== undefined) query.set("team_id", String(params.teamId));
  if (params.season !== undefined) query.set("season", String(params.season));
  if (params.isActive !== undefined) query.set("is_active", String(params.isActive));
  if (params.name) query.set("name", params.name);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  return request(`/api/afl/players${qs ? `?${qs}` : ""}`);
}

export async function fetchPlayer(playerId: number): Promise<PlayerSummary | null> {
  try {
    return await request(`/api/afl/players/${playerId}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export function fetchPlayerGames(playerId: number, params: { season?: number; limit?: number; offset?: number } = {}): Promise<PlayerGames> {
  const query = new URLSearchParams();
  if (params.season !== undefined) query.set("season", String(params.season));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const qs = query.toString();
  return request(`/api/afl/players/${playerId}/games${qs ? `?${qs}` : ""}`);
}

export function fetchPlayerForm(playerId: number, recentGames = 10): Promise<PlayerForm> {
  return request(`/api/afl/players/${playerId}/form?recent_games=${recentGames}`);
}

export async function fetchMatchPlayers(matchId: number): Promise<MatchPlayers | null> {
  try {
    return await request(`/api/afl/matches/${matchId}/players`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

// --- Player prop models (disposal-prediction stage) ---
// Historical model research only — never live betting advice. See
// app/player_modelling/disposal_cli.py and app/api/routes/player_models.py.

export interface PlayerModelRunSummary {
  model_name: string;
  market: string;
  is_promoted: boolean;
  distribution_method: string;
  feature_names: string[];
  tune_start_year: number;
  tune_end_year: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  run_at: string;
  overall_mae: number | null;
  overall_rmse: number | null;
  overall_bias: number | null;
  evaluation_n: number | null;
}

export interface PlayerModelRunList {
  is_research_only: boolean;
  runs: PlayerModelRunSummary[];
}

export interface DisposalSeasonMetric {
  season_year: number;
  n: number;
  mae: number;
  rmse: number;
  bias: number;
}

export interface DisposalBaselineComparison {
  model_name: string;
  mae: number;
  rmse: number;
  bias: number;
}

export interface DisposalBacktestSummary {
  is_research_only: boolean;
  promoted_model: PlayerModelRunSummary;
  baselines: DisposalBaselineComparison[];
  candidates: DisposalBaselineComparison[];
  season_breakdown: DisposalSeasonMetric[];
  within_2: number;
  within_5: number;
  within_10: number;
  median_ae: number;
}

export interface DisposalThresholdCalibration {
  threshold: number;
  n: number;
  brier: number;
  log_loss: number;
  ece: number | null;
  calibration: { bucket: string; n: number; avg_predicted: number | null; actual_rate: number | null }[];
}

export interface DisposalIntervalCalibration {
  coverage_target: number;
  n: number;
  empirical_coverage: number;
  mean_width: number;
}

export interface DisposalCalibrationReport {
  is_research_only: boolean;
  model_name: string;
  distribution_method: string;
  thresholds: DisposalThresholdCalibration[];
  intervals: DisposalIntervalCalibration[];
}

export interface DisposalPlayerPrediction {
  match_id: number;
  season_year: number;
  games_of_history: number;
  predicted_mean: number;
  actual_disposals: number;
  confidence_tier: string;
  interval_50: [number, number];
  interval_80: [number, number];
  prob_20_plus: number;
  prob_25_plus: number;
  prob_30_plus: number;
  prob_35_plus: number;
}

export interface DisposalPlayerHistory {
  is_research_only: boolean;
  player: PlayerSummary;
  model_name: string;
  predictions: DisposalPlayerPrediction[];
}

export async function fetchPlayerModelRuns(): Promise<PlayerModelRunList | null> {
  try {
    return await request("/api/player-models");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export async function fetchDisposalBacktestSummary(): Promise<DisposalBacktestSummary | null> {
  try {
    return await request("/api/player-models/disposals/backtest");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export async function fetchDisposalCalibration(modelName?: string): Promise<DisposalCalibrationReport | null> {
  try {
    const qs = modelName ? `?model_name=${encodeURIComponent(modelName)}` : "";
    return await request(`/api/player-models/disposals/calibration${qs}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export async function fetchDisposalPlayerHistory(playerId: number, modelName?: string): Promise<DisposalPlayerHistory | null> {
  try {
    const qs = modelName ? `?model_name=${encodeURIComponent(modelName)}` : "";
    return await request(`/api/player-models/disposals/players/${playerId}${qs}`);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 503 || err.status === 404)) {
      return null;
    }
    throw err;
  }
}

// --- Goal model (goal-prediction stage) ---

export interface GoalModelRunSummary {
  model_name: string;
  market: string;
  is_promoted: boolean;
  distribution_kind: string;
  feature_names: string[];
  tune_start_year: number;
  tune_end_year: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  run_at: string;
  overall_mae: number | null;
  overall_rmse: number | null;
  overall_bias: number | null;
  evaluation_n: number | null;
}

export interface GoalSeasonMetric {
  season_year: number;
  n: number;
  mae: number;
  bias: number;
}

export interface GoalBaselineComparison {
  model_name: string;
  mae: number | null;
  rmse: number | null;
  bias: number | null;
}

export interface ZeroGoalCalibration {
  brier: number;
  log_loss: number;
  ece: number | null;
  mean_predicted_p0: number;
  actual_p0: number;
}

export interface GoalBacktestSummary {
  is_research_only: boolean;
  promoted_model: GoalModelRunSummary;
  baselines: GoalBaselineComparison[];
  candidates: GoalBaselineComparison[];
  season_breakdown: GoalSeasonMetric[];
  zero_goal: ZeroGoalCalibration;
}

export interface GoalThresholdCalibration {
  threshold: number;
  n: number;
  n_positive: number;
  brier: number;
  log_loss: number;
  ece: number | null;
}

export interface GoalCalibrationReport {
  is_research_only: boolean;
  model_name: string;
  distribution_kind: string;
  thresholds: GoalThresholdCalibration[];
}

export async function fetchGoalBacktestSummary(): Promise<GoalBacktestSummary | null> {
  try {
    return await request("/api/player-models/goals/backtest");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export async function fetchGoalCalibration(modelName?: string): Promise<GoalCalibrationReport | null> {
  try {
    const qs = modelName ? `?model_name=${encodeURIComponent(modelName)}` : "";
    return await request(`/api/player-models/goals/calibration${qs}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return null;
    }
    throw err;
  }
}

export interface GoalTeamDiagnostic {
  match_id: number;
  team_id: number;
  sum_predicted_goals: number;
  team_expected_goals: number;
  gap: number;
}

export async function fetchGoalUpcomingTeamDiagnostic(): Promise<GoalTeamDiagnostic[]> {
  try {
    return await request("/api/player-models/goals/upcoming-team-diagnostic");
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return [];
    }
    throw err;
  }
}

// --- Live player projections (live-projection stage) ---
// Real, current pre-match projections for upcoming AFL matches. Distinct
// from the "Player Models" research pages above, which only ever describe
// HISTORICAL backtested performance.

export type ExpectedLineupStatus = "expected_in" | "expected_out" | "uncertain";
export type ConfidenceTierLive = "insufficient_history" | "lower_confidence" | "moderate_confidence" | "higher_confidence";
export type SelectionStatus =
  | "placeholder"
  | "named_in_squad"
  | "confirmed_selected"
  | "emergency"
  | "substitute"
  | "confirmed_out"
  | "uncertain";
export type AnnouncementState = "teams_not_announced" | "squad_announced" | "final_team_confirmed";
export type LineupFilter = "confirmed_only" | "confirmed_plus_expected" | "include_uncertain";

export interface ExpectedLineup {
  id: number;
  match_id: number;
  player_id: number;
  player_name: string;
  team_id: number;
  status: ExpectedLineupStatus;
  selection_status: SelectionStatus;
  is_confirmed: boolean;
  recorded_at: string;
  source: string;
  source_timestamp: string | null;
  source_reference: string | null;
  is_manual_override: boolean;
  note: string | null;
  substitute_risk: boolean;
  returning_from_injury: boolean;
  role_note: string | null;
  expected_tog_adjustment: number | null;
}

export interface ExpectedLineupInput {
  player_id: number;
  team_id: number;
  status: ExpectedLineupStatus;
  selection_status?: SelectionStatus;
  note?: string | null;
  substitute_risk?: boolean;
  returning_from_injury?: boolean;
  role_note?: string | null;
  expected_tog_adjustment?: number | null;
}

export function fetchMatchLineup(matchId: number): Promise<ExpectedLineup[]> {
  return request(`/api/afl/matches/${matchId}/lineup`);
}

export function setMatchLineup(matchId: number, playerId: number, input: ExpectedLineupInput): Promise<ExpectedLineup> {
  return request(`/api/afl/matches/${matchId}/lineup/${playerId}`, { method: "PUT", body: JSON.stringify(input) });
}

export function deleteMatchLineup(matchId: number, playerId: number): Promise<void> {
  return request(`/api/afl/matches/${matchId}/lineup/${playerId}`, { method: "DELETE" });
}

export interface LineupSummary {
  match_id: number;
  announcement_state: AnnouncementState;
  n_confirmed_selected: number;
  n_named_in_squad: number;
  n_emergency: number;
  n_substitute: number;
  n_confirmed_out: number;
  n_uncertain: number;
  n_placeholder: number;
  n_manual_overrides: number;
  last_updated: string | null;
}

export function fetchLineupSummary(matchId: number): Promise<LineupSummary> {
  return request(`/api/afl/matches/${matchId}/lineup/summary`);
}

export interface RosterSuggestion {
  player_id: number;
  display_name: string;
  last_match_id: number;
  last_played_at: string;
}

export function fetchSuggestedRoster(matchId: number, teamId: number): Promise<RosterSuggestion[]> {
  return request(`/api/afl/matches/${matchId}/lineup/suggested-roster?team_id=${teamId}`);
}

export interface BulkApplyEntry {
  player_id: number;
  team_id: number;
  selection_status: SelectionStatus;
  note?: string | null;
}

export interface BulkApplyResult {
  created: number[];
  updated: number[];
  status_changed: [number, string, string][];
  skipped_manual_override: number[];
  unresolved: string[];
  ambiguous: string[];
}

export function bulkApplyLineup(
  matchId: number,
  entries: BulkApplyEntry[],
  options: { source?: string; allowOverrideManual?: boolean } = {}
): Promise<BulkApplyResult> {
  return request(`/api/afl/matches/${matchId}/lineup/bulk-apply`, {
    method: "POST",
    body: JSON.stringify({ entries, source: options.source ?? "manual_bulk", allow_override_manual: options.allowOverrideManual ?? false }),
  });
}

export interface ThresholdProbability {
  probability: number;
  warning: string | null;
}

export interface DisposalProjection {
  is_research_only: boolean;
  match_id: number;
  player_id: number;
  player_name: string;
  team_id: number;
  team_name: string;
  round_number: number;
  season_year: number;
  scheduled_start: string;
  model_name: string;
  model_version: string;
  generated_at: string;
  data_cutoff: string;
  lineup_status: ExpectedLineupStatus;
  selection_status: SelectionStatus;
  is_confirmed: boolean;
  games_of_history: number;
  expected: number;
  median: number;
  interval_50: [number, number];
  interval_80: [number, number];
  interval_90: [number, number];
  thresholds: Record<string, ThresholdProbability>;
  confidence_tier: ConfidenceTierLive;
  warnings: string[];
  is_stale: boolean;
  stale_reasons: string[];
  input_features: Record<string, number | null>;
}

export interface GoalProjection {
  is_research_only: boolean;
  match_id: number;
  player_id: number;
  player_name: string;
  team_id: number;
  team_name: string;
  round_number: number;
  season_year: number;
  scheduled_start: string;
  model_name: string;
  model_version: string;
  generated_at: string;
  data_cutoff: string;
  lineup_status: ExpectedLineupStatus;
  selection_status: SelectionStatus;
  is_confirmed: boolean;
  games_of_history: number;
  expected: number;
  thresholds: Record<string, ThresholdProbability>;
  scoring_archetype: string;
  confidence_tier: ConfidenceTierLive;
  warnings: string[];
  is_stale: boolean;
  stale_reasons: string[];
  input_features: Record<string, number | null>;
}

export interface MatchProjections {
  match_id: number;
  disposals: DisposalProjection[];
  goals: GoalProjection[];
}

export async function fetchMatchProjections(matchId: number): Promise<MatchProjections | null> {
  try {
    return await request(`/api/afl/matches/${matchId}/player-projections`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export interface PlayerProjection {
  disposals: DisposalProjection | null;
  goals: GoalProjection | null;
}

export async function fetchPlayerProjection(playerId: number): Promise<PlayerProjection | null> {
  try {
    return await request(`/api/afl/players/${playerId}/projection`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export interface UpcomingProjectionFilters {
  market?: "player_disposals" | "player_goals";
  round?: number;
  season?: number;
  teamId?: number;
  matchId?: number;
  confidence?: ConfidenceTierLive;
  minProbability?: number;
  threshold?: number;
  lineupFilter?: LineupFilter;
}

export async function fetchUpcomingProjections(
  filters: UpcomingProjectionFilters = {}
): Promise<{ disposals?: DisposalProjection[]; goals?: GoalProjection[] }> {
  const query = new URLSearchParams();
  if (filters.market) query.set("market", filters.market);
  if (filters.round !== undefined) query.set("round", String(filters.round));
  if (filters.season !== undefined) query.set("season", String(filters.season));
  if (filters.teamId !== undefined) query.set("team_id", String(filters.teamId));
  if (filters.matchId !== undefined) query.set("match_id", String(filters.matchId));
  if (filters.confidence) query.set("confidence", filters.confidence);
  if (filters.minProbability !== undefined) query.set("min_probability", String(filters.minProbability));
  if (filters.threshold !== undefined) query.set("threshold", String(filters.threshold));
  if (filters.lineupFilter) query.set("lineup_filter", filters.lineupFilter);
  const qs = query.toString();
  return request(`/api/afl/player-projections/upcoming${qs ? `?${qs}` : ""}`);
}

// --- Manual player-prop entry + Prop Insights ---

export type PlayerPropMarketType = "player_disposals" | "player_goals";
export type PlayerPropLineType = "over_under" | "multi_plus";

export interface PlayerPropMarketQuote {
  id: number;
  match_id: number;
  player_id: number;
  player_name: string;
  bookmaker_name: string;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  price_decimal: number;
  recorded_at: string;
  source: string;
}

export interface PlayerPropMarketInput {
  bookmaker_name: string;
  player_id: number;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  price_decimal: number;
}

export function fetchMatchPlayerProps(matchId: number): Promise<PlayerPropMarketQuote[]> {
  return request(`/api/afl/matches/${matchId}/player-props`);
}

export function createPlayerProp(matchId: number, input: PlayerPropMarketInput): Promise<PlayerPropMarketQuote> {
  return request(`/api/afl/matches/${matchId}/player-props`, { method: "POST", body: JSON.stringify(input) });
}

export function deletePlayerProp(propId: number): Promise<void> {
  return request(`/api/afl/player-props/${propId}`, { method: "DELETE" });
}

export type EdgeCategory = "no_meaningful_difference" | "small_difference" | "moderate_difference" | "larger_difference";

export interface PropInsight {
  id: number;
  player_id: number;
  player_name: string;
  match_id: number;
  round_number: number;
  season_year: number;
  bookmaker_name: string;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  recorded_at: string;
  source: string;
  model_probability: number;
  model_fair_odds: number;
  offered_odds: number;
  raw_implied_probability: number;
  devigged_probability: number | null;
  overround_removed: boolean;
  difference_pp: number;
  expected_value: number;
  edge_category: EdgeCategory;
  confidence_tier: ConfidenceTierLive;
  selection_status: SelectionStatus;
  is_confirmed: boolean;
  warnings: string[];
}

export function fetchPropInsights(
  params: { market?: PlayerPropMarketType; confidence?: ConfidenceTierLive; includeUncertain?: boolean } = {}
): Promise<PropInsight[]> {
  const query = new URLSearchParams();
  if (params.market) query.set("market", params.market);
  if (params.confidence) query.set("confidence", params.confidence);
  if (params.includeUncertain !== undefined) query.set("include_uncertain", String(params.includeUncertain));
  const qs = query.toString();
  return request(`/api/afl/prop-insights${qs ? `?${qs}` : ""}`);
}

// --- Normalized (best-price, multi-bookmaker) Prop Insights ---

export type OddsFreshness = "fresh" | "aging" | "stale";

export interface BookmakerQuote {
  bookmaker_name: string;
  price_decimal: number;
  recorded_at: string;
  freshness: OddsFreshness;
  source: string;
}

export interface PriceMovement {
  first_price: number;
  current_price: number;
  highest_price: number;
  lowest_price: number;
  last_movement_at: string;
}

export interface OpportunityComponents {
  difference: number;
  expected_value: number;
  confidence: number;
  freshness: number;
  lineup: number;
  calibration: number;
  penalty_multiplier: number;
  penalty_reasons: string[];
}

export interface NormalizedPropInsight {
  match_id: number;
  round_number: number;
  season_year: number;
  player_id: number;
  player_name: string;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  model_probability: number;
  model_fair_odds: number;
  best_price: number;
  best_bookmaker: string;
  raw_implied_probability: number;
  devigged_probability: number | null;
  overround_removed: boolean;
  difference_pp: number;
  expected_value: number;
  edge_category: EdgeCategory;
  confidence_tier: ConfidenceTierLive;
  selection_status: SelectionStatus;
  is_confirmed: boolean;
  warnings: string[];
  n_bookmakers: number;
  bookmakers: BookmakerQuote[];
  odds_freshness: OddsFreshness;
  price_movement: PriceMovement;
  opportunity_score: number;
  opportunity_components: OpportunityComponents;
}

export function fetchNormalizedPropInsights(
  params: {
    market?: PlayerPropMarketType;
    confidence?: ConfidenceTierLive;
    includeUncertain?: boolean;
    opportunitiesOnly?: boolean;
    matchId?: number;
  } = {}
): Promise<NormalizedPropInsight[]> {
  const query = new URLSearchParams();
  if (params.market) query.set("market", params.market);
  if (params.confidence) query.set("confidence", params.confidence);
  if (params.includeUncertain !== undefined) query.set("include_uncertain", String(params.includeUncertain));
  if (params.opportunitiesOnly !== undefined) query.set("opportunities_only", String(params.opportunitiesOnly));
  if (params.matchId !== undefined) query.set("match_id", String(params.matchId));
  const qs = query.toString();
  return request(`/api/afl/prop-insights/normalized${qs ? `?${qs}` : ""}`);
}
