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

// --- Bookmaker eligibility (Market Integrity stage, Sections 4-5, 13) ---

export type BookmakerEligibility = "included" | "excluded" | "informational_only";

export interface BookmakerSetting {
  id: number;
  name: string;
  provider_key: string | null;
  region: string | null;
  is_exchange: boolean;
  eligibility: BookmakerEligibility;
}

export function fetchBookmakerEligibility(): Promise<BookmakerSetting[]> {
  return request("/api/bookmakers/eligibility");
}

export function updateBookmakerEligibility(bookmakerId: number, eligibility: BookmakerEligibility): Promise<BookmakerSetting> {
  return request(`/api/bookmakers/${bookmakerId}/eligibility`, {
    method: "PATCH",
    body: JSON.stringify({ eligibility }),
  });
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

export interface BulkRemoveResult {
  removed: number[];
  not_found: number[];
}

export function bulkRemoveLineup(matchId: number, playerIds: number[]): Promise<BulkRemoveResult> {
  return request(`/api/afl/matches/${matchId}/lineup/bulk-remove`, {
    method: "POST",
    body: JSON.stringify({ player_ids: playerIds }),
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
  usage_regime: string | null;
  model_risk_flags: ModelRiskFlagV1[];
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
  usage_regime: string | null;
  model_risk_flags: ModelRiskFlagV1[];
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
  current_context: MatchContextItem[];
  tog_volatile: boolean | null;
  substitute_risk: boolean | null;
  returning_from_injury: boolean | null;
  role_note: string | null;
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
  is_exchange: boolean;
  eligibility: BookmakerEligibility;
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

export interface CalibrationMetrics {
  evaluated_threshold: number;
  ece: number;
  n: number;
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
  why_model_likes_it: string;
  calibration: CalibrationMetrics | null;
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

// --- Best Opportunities (Sections 6-11, 17-18 of the best-bets stage) ---

export type OpportunityType = "player" | "team";

export interface PriceIntegrityCheck {
  price_advantage_pct: number;
  band_pct: number;
  best_bookmaker: string;
  best_price: number;
  best_price_freshness: OddsFreshness;
  next_best_bookmaker: string;
  next_best_price: number;
  next_best_price_freshness: OddsFreshness;
  recorded_at_gap_seconds: number;
  passes_integrity: boolean;
  checks: Record<string, boolean>;
  issues: string[];
}

export type MarketMaturityTier = "early_market" | "developing_market" | "mature_market";

export interface MarketMaturity {
  tier: MarketMaturityTier;
  label: string;
  n_bookmakers: number;
  snapshot_count: number | null;
  hours_until_kickoff: number | null;
}

export type QualityTierName = "strong_candidate" | "worth_reviewing" | "speculative" | "do_not_headline";

export interface QualityTier {
  tier: QualityTierName;
  label: string;
  caveats: string[];
}

export interface PriceShopping {
  best_enabled: BookmakerQuote | null;
  next_best_enabled: BookmakerQuote | null;
  worst_enabled: BookmakerQuote | null;
}

export interface BestOpportunity {
  opportunity_type: OpportunityType;
  match_id: number;
  round_number: number;
  season_year: number;
  label: string;
  market_type: string;
  player_id: number | null;
  player_name: string | null;
  team_id: number | null;
  line_type: PlayerPropLineType | null;
  threshold: number | null;
  selection: string | null;
  line_value: number | null;
  model_probability: number;
  model_fair_odds: number;
  best_price: number;
  best_bookmaker: string;
  best_price_is_exchange: boolean;
  eligible_price_available: boolean;
  best_price_all_bookmakers: number | null;
  best_bookmaker_all_bookmakers: string | null;
  best_price_all_differs_from_enabled: boolean;
  price_shopping: PriceShopping | null;
  quote_source: string | null;
  market_implied_probability: number;
  devigged_probability: number | null;
  overround_removed: boolean;
  difference_pp: number;
  expected_value: number;
  edge_category: EdgeCategory | null;
  confidence_tier: ConfidenceTierLive;
  selection_status: SelectionStatus | null;
  is_confirmed: boolean | null;
  n_bookmakers: number;
  bookmakers: BookmakerQuote[];
  snapshot_count: number | null;
  odds_freshness: OddsFreshness;
  why_model_likes_it: string;
  calibration: CalibrationMetrics | null;
  warnings: string[];
  usage_regime: string | null;
  model_risk_flags: ModelRiskFlagV1[];
  opportunity_score: number;
  opportunity_components: OpportunityComponents;
  price_integrity: PriceIntegrityCheck | null;
  market_maturity: MarketMaturity | null;
  quality_tier: QualityTier | null;
}

export function fetchBestOpportunities(
  params: {
    marketScope?: "all" | "player" | "team";
    includeUncertain?: boolean;
    includeStale?: boolean;
    includeInsufficientHistory?: boolean;
    limit?: number;
  } = {}
): Promise<BestOpportunity[]> {
  const query = new URLSearchParams();
  if (params.marketScope) query.set("market_scope", params.marketScope);
  if (params.includeUncertain !== undefined) query.set("include_uncertain", String(params.includeUncertain));
  if (params.includeStale !== undefined) query.set("include_stale", String(params.includeStale));
  if (params.includeInsufficientHistory !== undefined) query.set("include_insufficient_history", String(params.includeInsufficientHistory));
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request(`/api/afl/best-opportunities${qs ? `?${qs}` : ""}`);
}

// --- Diversified Best Opportunities (Weekly Opportunity Discovery stage) ---

export interface OpportunityAlternateLine {
  threshold: number | null;
  line_type: PlayerPropLineType | null;
  label: string;
  model_probability: number;
  best_price: number;
  best_bookmaker: string;
  difference_pp: number;
  expected_value: number;
  n_bookmakers: number;
}

export interface RecentForm {
  stat_field: string;
  last5: number[];
  last10: number[];
  last5_avg: number | null;
  last10_avg: number | null;
  predicted_mean: number | null;
  hit_rate_description: string;
  form_disagreement_label: string | null;
  conservative_model_flag: string | null;
}

export interface DiversifiedOpportunity extends BestOpportunity {
  family_label: string;
  alternate_lines: OpportunityAlternateLine[];
  correlation_labels: string[];
  price_advantage_pct: number | null;
  recent_form: RecentForm | null;
  reason_codes: string[];
  reason_labels: string[];
  representative_score: number;
}

export interface WeeklySummary {
  round_number: number | null;
  n_opportunities_passing_gates: number;
  n_unique_players: number;
  n_unique_matches: number;
  n_bookmakers: number;
  best_difference_pp: number | null;
  best_price_advantage_pct: number | null;
}

export interface BookmakerCoverage {
  bookmaker_name: string;
  n_active_player_markets: number;
  n_matches_covered: number;
}

export interface DiversifiedOpportunitiesResponse {
  opportunities: DiversifiedOpportunity[];
  summary: WeeklySummary;
  bookmaker_coverage: BookmakerCoverage[];
}

export type OpportunityView = "overall" | "disposals" | "goals";

export function fetchDiversifiedOpportunities(
  params: {
    view?: OpportunityView;
    marketScope?: "all" | "player" | "team";
    includeUncertain?: boolean;
    includeStale?: boolean;
    includeInsufficientHistory?: boolean;
    onePerMatch?: boolean;
    onePerPlayer?: boolean;
    limit?: number | null;
  } = {}
): Promise<DiversifiedOpportunitiesResponse> {
  const query = new URLSearchParams();
  if (params.view) query.set("view", params.view);
  if (params.marketScope) query.set("market_scope", params.marketScope);
  if (params.includeUncertain !== undefined) query.set("include_uncertain", String(params.includeUncertain));
  if (params.includeStale !== undefined) query.set("include_stale", String(params.includeStale));
  if (params.includeInsufficientHistory !== undefined) query.set("include_insufficient_history", String(params.includeInsufficientHistory));
  if (params.onePerMatch !== undefined) query.set("one_per_match", String(params.onePerMatch));
  if (params.onePerPlayer !== undefined) query.set("one_per_player", String(params.onePerPlayer));
  if (params.limit !== undefined && params.limit !== null) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request(`/api/afl/best-opportunities/diversified${qs ? `?${qs}` : ""}`);
}

// --- Opportunity tiers: Best Opportunities / Worth Reviewing / All Available (product-quality stage) ---

export interface OpportunityTiersResponse {
  best: DiversifiedOpportunity[];
  worth_reviewing: DiversifiedOpportunity[];
  all_available: BestOpportunity[];
  exclusion_breakdown: Record<string, number>;
  n_candidates: number;
  n_hard_excluded: number;
  fallback_message: string | null;
}

export function fetchOpportunityTiers(
  params: { marketScope?: "all" | "player" | "team"; bestLimit?: number | null; worthReviewingLimit?: number | null; allAvailableLimit?: number | null } = {}
): Promise<OpportunityTiersResponse> {
  const query = new URLSearchParams();
  if (params.marketScope) query.set("market_scope", params.marketScope);
  if (params.bestLimit !== undefined && params.bestLimit !== null) query.set("best_limit", String(params.bestLimit));
  if (params.worthReviewingLimit !== undefined && params.worthReviewingLimit !== null) query.set("worth_reviewing_limit", String(params.worthReviewingLimit));
  if (params.allAvailableLimit !== undefined && params.allAvailableLimit !== null) query.set("all_available_limit", String(params.allAvailableLimit));
  const qs = query.toString();
  return request(`/api/afl/best-opportunities/tiers${qs ? `?${qs}` : ""}`);
}

// --- Multi Builder (product feature stage) ----------------------------------

export interface MultiReason {
  code: string;
  label: string;
}

export interface MultiLeg {
  opportunity_type: "player" | "team";
  label: string;
  market_type: string;
  player_id: number | null;
  player_name: string | null;
  team_id: number | null;
  bookmaker_price: number;
  model_probability: number;
  model_fair_odds: number;
  difference_pp: number;
  confidence_tier: string;
  selection_status: string | null;
  is_confirmed: boolean | null;
  odds_freshness: string;
  warnings: string[];
  reasons: MultiReason[];
  warning_codes: MultiReason[];
  usage_regime: string | null;
  model_risk_flags: ModelRiskFlagV1[];
  model_name: string | null;
  model_version: string | null;
  calibration_known: boolean;
  // null for player legs by convention (every player-market opportunity IS
  // the "over" side by construction — see backend best_opportunities.py) —
  // callers that need a selection string for a player leg (e.g. adding it
  // to Placed Bets) supply "over" themselves rather than reading a stored one.
  selection: string | null;
  threshold: number | null;
  line_type: string | null;
  line_value: number | null;
}

export type MultiMode = "high_probability" | "value";

// Best-effort joint-probability enrichment (backend app/pricing/
// same_game_pricing.py) — present only for combos with at most one team
// leg (h2h/total) and at least one player leg; null otherwise. Additive:
// indicative_combined_odds above is still the naive product, unchanged.
export interface SameGamePricing {
  model_joint_probability: number;
  model_joint_fair_odds: number;
  naive_independence_probability: number;
  correlation_adjustment_pp: number;
  dependence_validated: boolean;
  model_version: string;
  n_simulations: number;
  mc_standard_error: number;
}

export interface MultiOption {
  option_label: string;
  bookmaker: string;
  mode: MultiMode;
  n_legs: number;
  indicative_combined_odds: number;
  indicative_odds_label: string;
  indicative_odds_explanation: string;
  provisional: boolean;
  lineup_ready: boolean;
  correlation_warnings: string[];
  reason_codes: string[];
  average_confidence_component: number;
  lowest_leg_probability: number;
  average_leg_probability: number;
  legs: MultiLeg[];
  same_game_pricing: SameGamePricing | null;
}

export type MultiTierKey = "conservative" | "balanced" | "higher_return" | "longer_shot";

export interface BookmakerComparisonEntry {
  bookmaker: string;
  indicative_combined_odds: number;
  n_legs: number;
}

export interface MultiTier {
  tier: MultiTierKey;
  label: string;
  options: MultiOption[];
  unavailable_reason: string | null;
  bookmaker_comparison: BookmakerComparisonEntry[];
}

export type MatchReadinessState = "NOT_READY" | "PROVISIONAL" | "READY";

export interface MatchReadiness {
  match_id: number;
  state: MatchReadinessState;
  reasons: string[];
  missing_explanation: string;

  team_odds_fresh: boolean;
  player_props_exist: boolean;
  player_props_fresh: boolean;
  player_identities_resolved: boolean;
  provisional_roster_available: boolean;
  projections_generated: boolean;
  projections_current: boolean;
  official_teams_confirmed: boolean;
  usable_multi_legs: number;

  has_fresh_odds: boolean;
  has_projections: boolean;
  teams_confirmed: boolean;
}

export interface MatchMultiTiers {
  match_id: number;
  n_eligible_legs: number;
  bookmakers_available: string[];
  tiers: MultiTier[];
  readiness: MatchReadiness;
}

export function fetchMatchMultiBuilder(matchId: number, params: { confirmedOnly?: boolean; mode?: MultiMode } = {}): Promise<MatchMultiTiers> {
  const query = new URLSearchParams();
  if (params.confirmedOnly !== undefined) query.set("confirmed_only", String(params.confirmedOnly));
  if (params.mode !== undefined) query.set("mode", params.mode);
  const qs = query.toString();
  return request(`/api/afl/matches/${matchId}/multi-builder${qs ? `?${qs}` : ""}`);
}

export interface RoundMultiSummaryRow {
  match_id: number;
  home_team_name: string;
  away_team_name: string;
  scheduled_start: string;
  n_eligible_legs: number;
  n_bookmakers_available: number;
  tiers_available: MultiTierKey[];
  readiness: MatchReadiness;
  best_options_by_tier: Partial<Record<MultiTierKey, MultiOption | null>>;
}

export interface RoundMultiSummary {
  matches: RoundMultiSummaryRow[];
}

export function fetchRoundMultiSummary(params: { confirmedOnly?: boolean } = {}): Promise<RoundMultiSummary> {
  const query = new URLSearchParams();
  if (params.confirmedOnly !== undefined) query.set("confirmed_only", String(params.confirmedOnly));
  const qs = query.toString();
  return request(`/api/afl/multi-builder/round-summary${qs ? `?${qs}` : ""}`);
}

// --- Final Weekly Shortlist (Market Integrity stage, Sections 7-11, 22) ---

export interface FinalShortlistOpportunity extends BestOpportunity {
  family_label: string;
  alternate_lines: OpportunityAlternateLine[];
  correlation_labels: string[];
  reason_codes: string[];
  why_it_ranks_here: string[];
  caveats: string[];
}

export interface ExcludedOpportunity {
  label: string;
  opportunity_type: OpportunityType;
  reason: string;
}

export interface FinalShortlistResponse {
  opportunities: FinalShortlistOpportunity[];
  excluded: ExcludedOpportunity[];
  empty_state_reason: string | null;
  any_confirmed_player_lineups: boolean;
}

export function fetchFinalShortlist(
  params: { limit?: number | null; includeUnconfirmedPlayers?: boolean } = {}
): Promise<FinalShortlistResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined && params.limit !== null) query.set("limit", String(params.limit));
  if (params.includeUnconfirmedPlayers !== undefined) query.set("include_unconfirmed_players", String(params.includeUnconfirmedPlayers));
  const qs = query.toString();
  return request(`/api/afl/best-opportunities/final-shortlist${qs ? `?${qs}` : ""}`);
}

// --- Model vs Market Disagreements (Section 18 — NOT an opportunity list) ---

export type DisagreementDirection = "model_above_market" | "market_above_model";

export interface ModelMarketDisagreement {
  opportunity_type: OpportunityType;
  match_id: number;
  label: string;
  market_type: string;
  player_id: number | null;
  player_name: string | null;
  threshold: number | null;
  line_type: PlayerPropLineType | null;
  model_probability: number;
  model_predicted_mean: number | null;
  market_probability: number;
  overround_removed: boolean;
  difference_pp: number;
  direction: DisagreementDirection;
  confidence_tier: ConfidenceTierLive;
  best_price: number;
  best_bookmaker: string;
  bookmakers: BookmakerQuote[];
  n_bookmakers: number;
  recent_form: { last5: number[]; last10: number[]; last5_avg: number | null; last10_avg: number | null; ewma?: number | null } | null;
  calibration: CalibrationMetrics | null;
  odds_freshness: OddsFreshness;
  warnings: string[];
}

export function fetchModelMarketDisagreements(params: { thresholdPp?: number; limit?: number | null } = {}): Promise<ModelMarketDisagreement[]> {
  const query = new URLSearchParams();
  if (params.thresholdPp !== undefined) query.set("threshold_pp", String(params.thresholdPp));
  if (params.limit !== undefined && params.limit !== null) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request(`/api/afl/model-vs-market-disagreements${qs ? `?${qs}` : ""}`);
}

// --- Elite disposal player monitoring diagnostic (Section 19) ---

export interface PlayerBiasEntry {
  player_id: number;
  player_name: string;
  n_predictions: number;
  avg_actual: number;
  avg_predicted: number;
  bias: number;
}

export interface EliteDisposalBucket {
  bucket: string;
  label: string;
  n_players: number;
  n_predictions: number;
  avg_actual: number;
  avg_predicted: number;
  bias: number;
  mae: number;
  most_under_predicted_players: PlayerBiasEntry[];
}

export function fetchEliteDisposalDiagnostic(
  params: { currentOnly?: boolean; minNPredictions?: number } = {}
): Promise<EliteDisposalBucket[] | null> {
  const query = new URLSearchParams();
  if (params.currentOnly !== undefined) query.set("current_only", String(params.currentOnly));
  if (params.minNPredictions !== undefined) query.set("min_n_predictions", String(params.minNPredictions));
  const qs = query.toString();
  return request(`/api/afl/elite-disposal-diagnostic${qs ? `?${qs}` : ""}`);
}

// --- Weekly Bet Review + Decision Support stage ---

export interface ModelStrength {
  market_type: string;
  model_name: string;
  metrics: Record<string, number | null>;
  evaluation_sample: number;
  caveats: string[];
}

export interface CalibrationBand {
  band_label: string;
  avg_predicted: number | null;
  actual_rate: number | null;
  n: number;
  meets_min_sample: boolean;
}

export type DirectionClassification = "agrees_on_direction" | "disagrees_on_direction";

export interface DirectionAgreement {
  classification: DirectionClassification;
  model_favours_selection: boolean;
  market_favours_selection: boolean;
  description: string;
}

export interface ProjectionLineDistance {
  market_type: string;
  model_projection: number;
  line_value: number;
  distance: number;
  unit: string;
}

export interface PricePoint {
  bookmaker_name: string | null;
  price_decimal: number;
  model_estimated_ev: number;
}

export interface PriceSensitivity {
  model_fair_price: number;
  price_points: PricePoint[];
}

export type MovementDirection = "toward_model" | "away_from_model" | "unchanged";

export interface MarketMovement {
  first_price: number;
  first_observed_at: string;
  latest_price: number;
  latest_observed_at: string;
  best_current_price: number;
  model_fair_odds: number;
  direction: MovementDirection;
  description: string;
}

export interface BookmakerProbability {
  bookmaker_name: string;
  price_decimal: number;
  probability: number;
  overround_removed: boolean;
}

export interface Consensus {
  consensus_probability: number;
  n_bookmakers: number;
  n_devigged: number;
  spread: number;
  methodology: string;
  per_bookmaker: BookmakerProbability[];
}

export interface OutlierCheck {
  is_outlier: boolean;
  best_price: number;
  median_eligible_price: number;
  pct_difference: number;
  message: string | null;
}

export interface EvidenceSummary {
  evidence_codes: string[];
  evidence_labels: string[];
  caution_codes: string[];
  caution_labels: string[];
}

export interface WeeklyReviewOpportunity extends BestOpportunity {
  family_label: string | null;
  alternate_lines: OpportunityAlternateLine[];
  correlation_labels: string[];
  reason_codes: string[];
  why_it_ranks_here: string[];
  caveats: string[];
  model_strength: ModelStrength | null;
  calibration_band: CalibrationBand | null;
  direction_agreement: DirectionAgreement;
  projection_line_distance: ProjectionLineDistance | null;
  price_sensitivity: PriceSensitivity;
  market_movement: MarketMovement | null;
  consensus: Consensus | null;
  outlier_check: OutlierCheck | null;
  evidence_summary: EvidenceSummary;
  current_context: MatchContextItem[];
  context_conflict: ContextConflict | null;
}

export interface WeeklyReviewPage {
  final_shortlist: WeeklyReviewOpportunity[];
  strongest_player_opportunities: WeeklyReviewOpportunity[];
  strongest_team_opportunities: WeeklyReviewOpportunity[];
  model_vs_market_disagreements_count: number;
  markets_waiting_on_team_confirmation: WeeklyReviewOpportunity[];
  bookmaker_coverage: BookmakerCoverage[];
  weekly_summary: WeeklySummary;
  any_confirmed_player_lineups: boolean;
}

export function fetchWeeklyReviewPage(params: { shortlistLimit?: number; comparisonLimit?: number } = {}): Promise<WeeklyReviewPage> {
  const query = new URLSearchParams();
  if (params.shortlistLimit !== undefined) query.set("shortlist_limit", String(params.shortlistLimit));
  if (params.comparisonLimit !== undefined) query.set("comparison_limit", String(params.comparisonLimit));
  const qs = query.toString();
  return request(`/api/afl/weekly-review${qs ? `?${qs}` : ""}`);
}

export interface ShortlistSnapshotSummary {
  id: number;
  created_at: string;
  round_number: number | null;
  season_year: number | null;
  n_items: number;
  label: string | null;
}

export interface ShortlistSnapshotItem {
  id: number;
  rank: number;
  opportunity_type: OpportunityType;
  label: string;
  match_id: number;
  market_type: string;
  player_id: number | null;
  selection: string | null;
  threshold: number | null;
  line_value: number | null;
  line_type: string | null;
  best_price: number;
  best_bookmaker: string;
  recorded_at: string;
  model_probability: number;
  model_fair_odds: number;
  market_implied_probability: number;
  devigged_probability: number | null;
  overround_removed: boolean;
  difference_pp: number;
  expected_value: number;
  confidence_tier: ConfidenceTierLive;
  quality_tier: QualityTierName;
  market_maturity_tier: MarketMaturityTier | null;
  is_confirmed: boolean | null;
  model_name: string | null;
  model_version: string | null;
  n_bookmakers: number;
  reasons_json: { why_it_ranks_here: string[]; caveats: string[]; correlation_labels: string[] };
  actual_stat_value: number | null;
  match_result: "won" | "lost" | "push" | null;
  settled_at: string | null;
}

export interface ShortlistSnapshot {
  id: number;
  created_at: string;
  round_number: number | null;
  season_year: number | null;
  limit_requested: number | null;
  include_unconfirmed_players: boolean;
  n_items: number;
  label: string | null;
  items: ShortlistSnapshotItem[];
}

export function fetchShortlistSnapshots(limit = 50): Promise<ShortlistSnapshotSummary[]> {
  return request(`/api/afl/weekly-review/shortlist-snapshots?limit=${limit}`);
}

export function createShortlistSnapshot(params: { limit?: number | null; includeUnconfirmedPlayers?: boolean; label?: string } = {}): Promise<ShortlistSnapshot> {
  return request("/api/afl/weekly-review/shortlist-snapshots", {
    method: "POST",
    body: JSON.stringify({ limit: params.limit ?? null, include_unconfirmed_players: params.includeUnconfirmedPlayers ?? false, label: params.label ?? null }),
  });
}

export function fetchShortlistSnapshot(snapshotId: number): Promise<ShortlistSnapshot> {
  return request(`/api/afl/weekly-review/shortlist-snapshots/${snapshotId}`);
}

export function settleShortlistSnapshot(snapshotId: number): Promise<{ snapshot_id: number; settled_count: number }> {
  return request(`/api/afl/weekly-review/shortlist-snapshots/${snapshotId}/settle`, { method: "POST" });
}

export interface ShortlistRoundSummaryItem {
  label: string;
  opportunity_type: OpportunityType;
  best_price: number;
  best_bookmaker: string;
  model_probability: number;
  market_implied_probability: number;
  match_result: "won" | "lost" | "push" | null;
  actual_stat_value: number | null;
  flat_stake_pl: number | null;
}

export interface ShortlistRoundSummary {
  snapshot_id: number;
  round_number: number | null;
  season_year: number | null;
  n_items: number;
  n_settled: number;
  n_unresolved: number;
  n_won: number;
  n_lost: number;
  n_push: number;
  hypothetical_flat_stake_pl: number | null;
  n_unique_matches: number;
  n_team: number;
  n_player: number;
  confidence_tier_breakdown: Record<string, number>;
  quality_tier_breakdown: Record<string, number>;
  small_sample_warning: boolean;
  items: ShortlistRoundSummaryItem[];
}

export function fetchShortlistRoundSummary(snapshotId: number): Promise<ShortlistRoundSummary> {
  return request(`/api/afl/weekly-review/shortlist-snapshots/${snapshotId}/round-summary`);
}

// --- Real Market Tracking (Sections 18-19 of the market-logging stage) ---

export type SampleSizeLevel = "exploratory" | "low_confidence" | "still_developing" | "informative";

export interface DatasetSummary {
  total_observations: number;
  settled_observations: number;
  pending_observations: number;
  unique_player_matches: number;
  unique_players: number;
  unique_matches: number;
  unique_market_lines: number;
  bookmakers: string[];
  earliest_observed_at: string | null;
  latest_observed_at: string | null;
}

export interface ModelVsMarket {
  n_settled_binary: number;
  model_brier: number | null;
  model_log_loss: number | null;
  market_brier: number | null;
  market_log_loss: number | null;
  market_probability_source: string;
}

export interface CalibrationBucket {
  probability_range: string;
  n: number;
  mean_predicted: number | null;
  mean_actual: number | null;
}

export interface HypotheticalReturn {
  n_settled_binary: number;
  n_pushed: number;
  n_voided: number;
  total_profit_flat_stake: number;
  roi: number | null;
  win_rate: number | null;
  average_odds: number | null;
  average_model_probability: number | null;
  average_difference_pp: number | null;
}

export interface BucketResult {
  label: string;
  n_observations: number;
  n_unique_player_matches: number;
  returns: HypotheticalReturn;
  sample_size_level: SampleSizeLevel;
}

export interface RealMarketTrackingReport {
  label: string;
  summary: DatasetSummary;
  model_vs_market: ModelVsMarket;
  model_calibration: CalibrationBucket[];
  market_calibration: CalibrationBucket[];
  overall_return: HypotheticalReturn;
  edge_buckets: BucketResult[];
  confidence_buckets: BucketResult[];
  lineup_buckets: BucketResult[];
  timing_buckets: BucketResult[];
  overall_sample_level: SampleSizeLevel;
  coverage: CoverageMetrics;
  market_open_timing: MarketOpenTiming[];
}

export function fetchRealMarketTracking(params: { matchId?: number; marketType?: PlayerPropMarketType } = {}): Promise<RealMarketTrackingReport> {
  const query = new URLSearchParams();
  if (params.matchId !== undefined) query.set("match_id", String(params.matchId));
  if (params.marketType) query.set("market_type", params.marketType);
  const qs = query.toString();
  return request(`/api/afl/real-market-tracking${qs ? `?${qs}` : ""}`);
}

export interface QuoteHistoryEntry {
  observed_at: string;
  bookmaker_name: string;
  offered_odds: number;
  raw_implied_probability: number;
  devigged_probability: number | null;
  model_probability: number;
  difference_pp: number;
  confidence_tier: ConfidenceTierLive;
  selection_status_at_observation: string;
  market_result: string | null;
}

export function fetchQuoteHistory(params: {
  playerId: number;
  matchId: number;
  bookmakerId: number;
  marketType: PlayerPropMarketType;
  lineType: PlayerPropLineType;
  threshold: number;
}): Promise<QuoteHistoryEntry[]> {
  const query = new URLSearchParams({
    player_id: String(params.playerId),
    match_id: String(params.matchId),
    bookmaker_id: String(params.bookmakerId),
    market_type: params.marketType,
    line_type: params.lineType,
    threshold: String(params.threshold),
  });
  return request(`/api/afl/real-market-tracking/quote-history?${query.toString()}`);
}

export interface MarketMovement {
  player_id: number;
  player_name: string;
  match_id: number;
  bookmaker_id: number;
  bookmaker_name: string;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  first_odds: number;
  latest_odds: number;
  highest_odds: number;
  lowest_odds: number;
  first_difference_pp: number;
  latest_difference_pp: number;
  first_observed_at: string;
  latest_observed_at: string;
  n_observations: number;
}

export function fetchMarketMovement(params: { matchId?: number; playerId?: number } = {}): Promise<MarketMovement[]> {
  const query = new URLSearchParams();
  if (params.matchId !== undefined) query.set("match_id", String(params.matchId));
  if (params.playerId !== undefined) query.set("player_id", String(params.playerId));
  const qs = query.toString();
  return request(`/api/afl/real-market-tracking/movement${qs ? `?${qs}` : ""}`);
}

// --- Live Status (Sections 5-6, 18 of the live-operations stage) ----------

export interface CoverageMetrics {
  total_raw_quotes: number;
  frozen_observations: number;
  unique_player_matches: number;
  unique_matches: number;
  unique_market_lines: number;
  bookmakers: string[];
  market_families: string[];
  average_snapshots_per_player_market: number | null;
}

export interface MarketOpenTiming {
  player_id: number;
  player_name: string;
  match_id: number;
  bookmaker_id: number;
  bookmaker_name: string;
  market_type: PlayerPropMarketType;
  line_type: PlayerPropLineType;
  threshold: number;
  first_observed_at: string;
  first_hours_before_kickoff: number;
  latest_observed_at: string;
  latest_hours_before_kickoff: number;
  n_price_changes: number;
  n_observations: number;
}

export type MatchSimpleStatus =
  | "waiting_for_teams"
  | "waiting_for_bookmaker_markets"
  | "stale"
  | "ready"
  | "odds_available"
  | "completed_awaiting_player_stats"
  | "settled"
  | "completed_no_market_data";

export type MarketDiagnosisCategory =
  | "event_absent"
  | "not_yet_refreshed"
  | "event_no_props"
  | "disposal_market_absent"
  | "bookmaker_absent"
  | "odds_available";

export interface MatchMarketDiagnosis {
  match_id: number;
  category: MarketDiagnosisCategory;
  detail: string;
  would_be_skipped_this_cycle: boolean;
  hours_to_kickoff: number;
  disposals_available: boolean;
  goals_available: boolean;
  unique_player_count: number;
}

export interface MatchCoverageStatus {
  match_id: number;
  home_team_name: string;
  away_team_name: string;
  scheduled_start: string;
  match_status: string;
  simple_status: MatchSimpleStatus;
  lineup_announcement_state: AnnouncementState;
  projections_generated: boolean;
  bookmaker_event_exists: boolean;
  bookmaker_props_observed: boolean;
  bookmakers_observed: string[];
  n_quotes: number;
  last_odds_refresh: string | null;
  n_observations: number;
  n_observations_settled: number;
  n_observations_awaiting_settlement: number;
  disposals_available: boolean;
  goals_available: boolean;
  unique_player_count: number;
  diagnosis: MatchMarketDiagnosis;
}

export interface RoundSummary {
  n_upcoming_matches: number;
  n_matches_with_projections: number;
  n_matches_with_bookmaker_events: number;
  n_matches_with_prop_markets: number;
  n_unique_players_with_markets: number;
  n_real_quotes_stored: number;
  n_real_observations_stored: number;
  n_confirmed_lineups: number;
  n_placeholder_or_uncertain_lineups: number;
}

export type LiveCycleStepStatus = "success" | "warning" | "recoverable_failure" | "blocking_failure";

export interface LiveCycleStep {
  step: string;
  status: LiveCycleStepStatus;
  detail: string;
}

export interface LiveCycleRun {
  id: number;
  run_at: string;
  finished_at: string | null;
  overall_status: "ok" | "partial" | "blocked";
  steps: LiveCycleStep[];
  odds_credits_consumed: number | null;
  odds_credits_remaining: number | null;
  matches_affected: number;
  quotes_added: number;
  observations_added: number;
  observations_settled: number;
  team_odds_quotes_added: number;
  weather_snapshots_added: number;
}

export interface LiveStatusReport {
  round_summary: RoundSummary;
  matches: MatchCoverageStatus[];
  recent_runs: LiveCycleRun[];
}

export function fetchLiveStatus(): Promise<LiveStatusReport> {
  return request("/api/afl/live-status");
}

// --- Data freshness + "Refresh Data" (product-polish stage) ----------------

export type FreshnessStatus = "fresh" | "aging" | "stale" | "not_available";

export interface DataFreshnessItem {
  category: string;
  label: string;
  status: FreshnessStatus;
  last_refreshed: string | null;
  detail: string;
}

export interface DataFreshnessReport {
  items: DataFreshnessItem[];
}

// Read-only — never triggers a provider request, safe to call on page load.
export function fetchDataFreshness(): Promise<DataFreshnessReport> {
  return request("/api/afl/data-freshness");
}

// The ONLY call in this client that can trigger a paid external API
// request — deliberately a POST, only ever fired from an explicit user
// click on the "Refresh Data" button, never on page load.
export function triggerRefresh(): Promise<LiveCycleRun> {
  return request("/api/afl/refresh", { method: "POST" });
}

// --- Current Context + Team News Intelligence stage ------------------------

export type ContextType =
  | "confirmed_in"
  | "confirmed_out"
  | "injury"
  | "late_withdrawal"
  | "named_substitute"
  | "emergency"
  | "returning_player"
  | "limited_game_time_concern"
  | "weather"
  | "venue_condition"
  | "major_role_change"
  | "other";

export type ContextConfidence = "official" | "reputable_source" | "unverified";
export type ContextFreshness = "fresh" | "aging" | "stale";

export interface MatchContextItem {
  id: number;
  match_id: number;
  team_id: number | null;
  player_id: number | null;
  player_name: string | null;
  context_type: ContextType;
  context_type_label: string;
  confidence: ContextConfidence;
  confidence_label: string;
  source: string;
  source_reference: string | null;
  source_timestamp: string | null;
  recorded_at: string;
  summary: string;
  freshness: ContextFreshness;
  is_current: boolean;
}

export interface CreateContextItemInput {
  context_type: ContextType;
  source: string;
  summary: string;
  confidence: ContextConfidence;
  team_id?: number | null;
  player_id?: number | null;
  source_timestamp?: string | null;
  source_reference?: string | null;
  apply_to_lineup?: boolean;
}

export interface MatchContextApplyResult {
  item: MatchContextItem;
  lineup_updated: boolean;
  lineup_apply_note: string | null;
}

export interface WeatherSnapshot {
  match_id: number;
  venue_id: number;
  fetched_at: string;
  forecast_for: string;
  temperature_c: number | null;
  rain_probability_pct: number | null;
  expected_rainfall_mm: number | null;
  wind_speed_kph: number | null;
  wind_gust_kph: number | null;
  severe_weather_warning: boolean;
  severe_weather_note: string | null;
  source: string;
}

export interface WeatherDiagnostic {
  match_id: number;
  weather_available: boolean;
  rain_probability_pct: number | null;
  wind_gust_kph: number | null;
  is_wet: boolean;
  is_windy: boolean;
  projected_total_points: number | null;
  historical_sample_overall: number;
  historical_mae_overall: number | null;
  historical_sample_similar_condition: number;
  historical_mae_similar_condition: number | null;
  has_sufficient_data: boolean;
  note: string;
}

export interface ContextConflict {
  codes: string[];
  labels: string[];
  latest_context_at: string | null;
  model_generated_at: string | null;
}

export interface MatchContextPanel {
  match_id: number;
  current_context: MatchContextItem[];
  weather: WeatherSnapshot | null;
  last_updated: string | null;
}

export interface RoundContextMatch {
  match_id: number;
  round_number: number;
  season_year: number;
  scheduled_start: string;
  home_team_name: string;
  away_team_name: string;
  lineup_announcement_state: string;
  n_confirmed_in: number;
  n_confirmed_out: number;
  n_substitutes: number;
  n_other_context_items: number;
  weather: WeatherSnapshot | null;
  n_stale_projections: number;
}

export interface RoundContextDashboard {
  round_number: number | null;
  season_year: number | null;
  matches: RoundContextMatch[];
}

export function fetchMatchContextHistory(matchId: number): Promise<MatchContextItem[]> {
  return request(`/api/afl/matches/${matchId}/context`);
}

export function fetchMatchContextCurrent(matchId: number): Promise<MatchContextItem[]> {
  return request(`/api/afl/matches/${matchId}/context/current`);
}

export function createMatchContextItem(matchId: number, input: CreateContextItemInput): Promise<MatchContextApplyResult> {
  return request(`/api/afl/matches/${matchId}/context`, { method: "POST", body: JSON.stringify(input) });
}

export function fetchMatchContextPanel(matchId: number): Promise<MatchContextPanel> {
  return request(`/api/afl/matches/${matchId}/context-panel`);
}

export function fetchMatchWeather(matchId: number): Promise<WeatherSnapshot | null> {
  return request(`/api/afl/matches/${matchId}/weather`);
}

export function refreshWeather(): Promise<{ matches_considered: number; snapshots_created: number; skipped_no_venue: number[]; skipped_no_coordinates: number[]; skipped_too_far_out: number[]; errors: string[] }> {
  return request("/api/afl/weather/refresh", { method: "POST" });
}

export function fetchWeatherDiagnostic(matchId: number): Promise<WeatherDiagnostic> {
  return request(`/api/afl/matches/${matchId}/weather-diagnostic`);
}

export function fetchContextDashboard(): Promise<RoundContextDashboard> {
  return request("/api/afl/context-dashboard");
}

// --- Placed Bets tracker (personal record-keeping only — never feeds
// model training or ranking; see backend app/player_modelling/placed_bets.py) ---

export type PlacedBetSourceMode = "high_probability" | "best_value" | "best_opportunity" | "final_shortlist" | "manual";
export type PlacedBetStatus = "pending" | "won" | "lost" | "push" | "void";

export interface PlacedBetCreateInput {
  match_id: number;
  opportunity_type: "player" | "team";
  label: string;
  selection: string;
  market_type: string;
  bookmaker: string;
  odds_taken: number;
  model_probability: number;
  model_fair_odds: number;
  confidence_tier: string;
  source_mode: PlacedBetSourceMode;
  player_id?: number | null;
  line_type?: string | null;
  threshold?: number | null;
  line_value?: number | null;
  stake?: number | null;
  lineup_status?: string | null;
  notes?: string | null;
  placed_at?: string | null;
  model_version?: string | null;
  multi_group_id?: string | null;
  multi_tier?: string | null;
  multi_indicative_odds?: number | null;
}

export interface PlacedBet extends PlacedBetCreateInput {
  id: number;
  status: PlacedBetStatus;
  actual_stat_value: number | null;
  settled_at: string | null;
}

export function fetchPlacedBets(status?: PlacedBetStatus): Promise<PlacedBet[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/placed-bets${qs}`);
}

export function createPlacedBet(input: PlacedBetCreateInput): Promise<PlacedBet> {
  return request("/api/placed-bets", { method: "POST", body: JSON.stringify(input) });
}

export function deletePlacedBet(betId: number): Promise<void> {
  return request(`/api/placed-bets/${betId}`, { method: "DELETE" });
}

export interface PlacedBetSplit {
  label: string;
  n_settled: number;
  wins: number;
  losses: number;
  voids: number;
  hit_rate: number | null;
  exploratory: boolean;
}

export interface PlacedBetAnalytics {
  n_total_settled: number;
  wins: number;
  losses: number;
  voids: number;
  hit_rate: number | null;
  avg_odds_taken: number | null;
  flat_stake_units: number | null;
  flat_stake_roi_pct: number | null;
  exploratory: boolean;
  min_sample_for_labeled: number;
  by_source_mode: PlacedBetSplit[];
  by_market_type: PlacedBetSplit[];
  by_probability_bucket: PlacedBetSplit[];
  by_confidence_tier: PlacedBetSplit[];
}

export function fetchPlacedBetAnalytics(): Promise<PlacedBetAnalytics> {
  return request("/api/placed-bets/analytics");
}

// --- Model Registry + Prospective Live Evaluation (B2B pricing engine) -----
// Two strictly separate datasets — each response carries its own
// dataset_label ("Historical backtest" vs "Prospective live evaluation")
// precisely so a consumer never blends them. Read-only; no model/ranking
// logic lives here.

export type ModelRunStatus = "champion" | "previous_champion" | "challenger" | "rejected";

export interface ModelRunSummary {
  model_name: string;
  model_version: string;
  market: string;
  status: ModelRunStatus;
  run_at: string;
  tune_start_year: number;
  tune_end_year: number;
  evaluation_start_year: number;
  evaluation_end_year: number;
  sample_size: number | null;
  point_metrics: Record<string, number | null>;
  calibration_metrics: Record<string, { brier: number | null; ece: number | null }>;
  promotion_reason: string | null;
}

export interface PromotionEvent {
  market: string;
  previous_champion_model_name: string | null;
  previous_champion_model_version: string | null;
  new_champion_model_name: string;
  new_champion_model_version: string;
  promoted_at: string;
  evidence_summary: string;
  evaluation_metrics: Record<string, unknown>;
}

export interface DisposalHeadToHead {
  ridge: ModelRunSummary | null;
  huber: ModelRunSummary | null;
  ridge_high_volume_bias: Record<string, number>;
  huber_high_volume_bias: Record<string, number>;
  ridge_low_history_bias: Record<string, number>;
  huber_low_history_bias: Record<string, number>;
}

export interface ModelRegistry {
  dataset_label: string;
  disposal_models: ModelRunSummary[];
  goal_models: ModelRunSummary[];
  team_models: ModelRunSummary[];
  disposal_head_to_head: DisposalHeadToHead;
  promotion_events: PromotionEvent[];
}

export function fetchModelRegistry(): Promise<ModelRegistry> {
  return request("/api/v1/model-registry");
}

export interface ProspectiveSplit {
  label: string;
  n_settled: number;
  n_unique_events: number;
  model_brier: number | null;
  market_brier: number | null;
  model_log_loss: number | null;
  market_log_loss: number | null;
  model_calibration_ece: number | null;
  n_with_market_consensus: number;
  exploratory: boolean;
}

export interface ProspectiveEvaluation {
  dataset_label: string;
  has_settled_data: boolean;
  n_frozen_total: number;
  n_settled: number;
  n_unique_player_match_events: number;
  overall: ProspectiveSplit | null;
  by_market_family: ProspectiveSplit[];
  by_probability_bucket: ProspectiveSplit[];
  by_model_version: ProspectiveSplit[];
  message: string;
}

export function fetchProspectiveEvaluation(): Promise<ProspectiveEvaluation> {
  return request("/api/v1/model-registry/prospective-evaluation");
}

// Same Game Multi's own prospective evaluation — a separate dataset from
// ProspectiveEvaluation above (multi-leg, model-vs-naive-independence and
// model-vs-bookmaker rather than model-vs-market-consensus). See backend
// app/player_modelling/sgm_prospective_evaluation.py.
export interface SgmProspectiveSplit {
  label: string;
  n_settled: number;
  n_unique_combos: number;
  model_brier: number | null;
  naive_brier: number | null;
  model_log_loss: number | null;
  naive_log_loss: number | null;
  model_calibration_ece: number | null;
  bookmaker_brier: number | null;
  bookmaker_log_loss: number | null;
  n_with_bookmaker_price: number;
  exploratory: boolean;
}

export interface SgmProspectiveEvaluation {
  dataset_label: string;
  has_settled_data: boolean;
  n_frozen_total: number;
  n_settled: number;
  n_unique_combos: number;
  overall: SgmProspectiveSplit | null;
  by_n_legs: SgmProspectiveSplit[];
  by_leg_combination: SgmProspectiveSplit[];
  by_correlation_adjustment_magnitude: SgmProspectiveSplit[];
  by_snapshot_horizon: SgmProspectiveSplit[];
  message: string;
}

export function fetchSgmProspectiveEvaluation(): Promise<SgmProspectiveEvaluation> {
  return request("/api/v1/model-registry/sgm-prospective-evaluation");
}

// --- B2B Pricing API (/api/v1/pricing/*, /api/v1/market-intelligence/*) ----
// Pure model belief (pricing) vs bookmaker comparison (market intelligence)
// are separate endpoints by design — see backend app/pricing/ module docs.

export interface ModelProvenanceV1 {
  model_name: string;
  model_version: string;
  generated_at: string;
  data_cutoff: string;
}

export interface ThresholdPriceV1 {
  threshold: number;
  line_type: string;
  probability: number;
  fair_odds: number;
}

export interface LinePriceV1 {
  line_value: number;
  home_team: string;
  away_team: string;
  home_probability: number;
  away_probability: number;
  home_fair_odds: number;
  away_fair_odds: number;
}

export interface TotalPriceV1 {
  line_value: number;
  over_probability: number;
  under_probability: number;
  over_fair_odds: number;
  under_fair_odds: number;
}

export interface TeamMarketPriceV1 {
  match_id: number;
  home_team: string;
  away_team: string;
  provenance: ModelProvenanceV1;
  home_win_probability: number;
  draw_probability: number;
  away_win_probability: number;
  home_fair_odds: number;
  draw_fair_odds: number;
  away_fair_odds: number;
  expected_margin: number;
  expected_total_points: number;
  home_expected_score: number;
  away_expected_score: number;
  lines: LinePriceV1[];
  totals: TotalPriceV1[];
}

export interface CalibrationInfoV1 {
  market_type: string;
  requested_threshold: number;
  evaluated_threshold: number;
  ece: number;
  n: number;
}

export interface ModelRiskFlagV1 {
  code: string;
  description: string;
}

export interface DisposalPriceV1 {
  match_id: number;
  player_id: number;
  player_name: string;
  team_id: number;
  provenance: ModelProvenanceV1;
  lineup_status: string;
  confidence_tier: string;
  games_of_history: number;
  expected: number;
  distribution_method: string;
  distribution_params: Record<string, number | null>;
  interval_50: [number, number];
  interval_80: [number, number];
  interval_90: [number, number];
  thresholds: ThresholdPriceV1[];
  calibration: CalibrationInfoV1 | null;
  warnings: string[];
  is_stale: boolean;
  stale_reasons: string[];
  usage_regime: string | null;
  usage_change_score: number | null;
  model_risk_flags: ModelRiskFlagV1[];
}

export interface GoalPriceV1 {
  match_id: number;
  player_id: number;
  player_name: string;
  team_id: number;
  provenance: ModelProvenanceV1;
  lineup_status: string;
  confidence_tier: string;
  games_of_history: number;
  expected: number;
  distribution_kind: string;
  distribution_params: Record<string, number | null>;
  scoring_archetype: string;
  thresholds: ThresholdPriceV1[];
  calibration: CalibrationInfoV1 | null;
  warnings: string[];
  is_stale: boolean;
  stale_reasons: string[];
  usage_regime: string | null;
  usage_change_score: number | null;
  model_risk_flags: ModelRiskFlagV1[];
}

export interface MatchPricing {
  match_id: number;
  team: TeamMarketPriceV1;
  disposals: DisposalPriceV1[];
  goals: GoalPriceV1[];
}

export interface RoundPricing {
  round_number: number | null;
  season_year: number | null;
  n_matches: number;
  teams: TeamMarketPriceV1[];
  disposals: DisposalPriceV1[];
  goals: GoalPriceV1[];
}

export function fetchMatchPricing(matchId: number): Promise<MatchPricing> {
  return request(`/api/v1/pricing/afl/matches/${matchId}`);
}

export function fetchCurrentRoundPricing(): Promise<RoundPricing> {
  return request("/api/v1/pricing/afl/current-round");
}

export interface BookLineV1 {
  bookmaker_name: string;
  price_decimal: number;
  recorded_at: string;
  eligibility: string;
}

export interface ConsensusV1 {
  consensus_probability: number;
  n_bookmakers: number;
  n_devigged: number;
  spread: number;
  methodology: string;
}

export interface OutlierV1 {
  is_outlier: boolean;
  best_price: number;
  median_eligible_price: number;
  pct_difference: number;
  message: string | null;
}

export interface MarketIntelligence {
  has_market: boolean;
  n_bookmakers: number;
  best_price: number | null;
  best_bookmaker: string | null;
  consensus: ConsensusV1 | null;
  outlier: OutlierV1 | null;
  model_probability: number;
  market_implied_probability: number | null;
  difference_pp: number | null;
  books: BookLineV1[];
}

export function fetchPlayerMarketIntelligence(
  playerId: number, marketType: "player_disposals" | "player_goals", matchId: number, threshold: number, lineType = "over_under"
): Promise<MarketIntelligence> {
  return request(`/api/v1/market-intelligence/afl/players/${playerId}/${marketType}?match_id=${matchId}&threshold=${threshold}&line_type=${lineType}`);
}

export function fetchTeamMarketIntelligence(
  matchId: number, marketType: "h2h" | "line" | "total", selection: string, lineValue: number | null = null
): Promise<MarketIntelligence> {
  const qs = new URLSearchParams({ selection });
  if (lineValue !== null) qs.set("line_value", String(lineValue));
  return request(`/api/v1/market-intelligence/afl/matches/${matchId}/team/${marketType}?${qs.toString()}`);
}

export interface StaleWarning {
  category: string;
  detail: string;
}

export interface IntegrationHealth {
  status: string;
  generated_at: string;
  last_fixture_refresh: string | null;
  last_odds_refresh: string | null;
  current_round: number | null;
  current_season_year: number | null;
  promoted_models: Record<string, string>;
  stale_warnings: StaleWarning[];
}

export function fetchIntegrationHealth(): Promise<IntegrationHealth> {
  return request("/api/v1/integration-health");
}

// --- B2B Market Anomaly / Trading QA Engine (/api/v1/market-monitor/*) -----

export interface AnomalyBookmakerPrice {
  bookmaker_name: string;
  price_decimal: number;
  recorded_at: string;
  eligibility: string;
}

export interface AnomalyAlert {
  alert_type: string;
  severity: string;
  reason_code: string;
  detail: string;
  match_id: number;
  home_team: string;
  away_team: string;
  player_id: number | null;
  player_name: string | null;
  team_id: number | null;
  market_type: string;
  selection: string | null;
  threshold: number | null;
  line_value: number | null;
  model_probability: number | null;
  model_fair_odds: number | null;
  market_consensus_probability: number | null;
  bookmaker_prices: AnomalyBookmakerPrice[];
  freshness: string | null;
  model_version: string | null;
  lineup_status: string | null;
  context_state: string | null;
  model_risk_flags: ModelRiskFlagV1[];
  generated_at: string;
}

export interface AnomalyListResponse {
  generated_at: string;
  n_matches_scanned: number;
  total: number;
  alerts: AnomalyAlert[];
}

export interface MatchAnomaliesResponse {
  match_id: number;
  home_team: string;
  away_team: string;
  alerts: AnomalyAlert[];
}

export interface AnomalyTypeCount {
  alert_type: string;
  count: number;
}

export interface SeverityCount {
  severity: string;
  count: number;
}

export interface AnomalySummary {
  generated_at: string;
  n_matches_scanned: number;
  total_anomalies: number;
  by_type: AnomalyTypeCount[];
  by_severity: SeverityCount[];
}

export function fetchAnomalies(
  params: { alertType?: string; severity?: string; matchId?: number; bookmakerName?: string; limit?: number } = {}
): Promise<AnomalyListResponse> {
  const qs = new URLSearchParams();
  if (params.alertType) qs.set("alert_type", params.alertType);
  if (params.severity) qs.set("severity", params.severity);
  if (params.matchId !== undefined) qs.set("match_id", String(params.matchId));
  if (params.bookmakerName) qs.set("bookmaker_name", params.bookmakerName);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return request(`/api/v1/market-monitor/anomalies${q ? `?${q}` : ""}`);
}

export function fetchMatchAnomalies(matchId: number): Promise<MatchAnomaliesResponse> {
  return request(`/api/v1/market-monitor/matches/${matchId}`);
}

export function fetchAnomalySummary(): Promise<AnomalySummary> {
  return request("/api/v1/market-monitor/summary");
}

// --- Alert Precision + Trader Prioritisation (cases) ------------------------

export interface PriorityComponent {
  name: string;
  raw_value: number | null;
  normalized: number;
  weight: number;
  contribution: number;
  explanation: string;
}

export interface AnomalyCase {
  case_id: string;
  match_id: number;
  home_team: string;
  away_team: string;
  player_id: number | null;
  player_name: string | null;
  team_id: number | null;
  market_type: string;
  selection: string | null;
  threshold: number | null;
  line_value: number | null;
  primary_alert: AnomalyAlert;
  supporting_alert_types: string[];
  alerts: AnomalyAlert[];
  bookmakers: string[];
  first_detected: string;
  latest_detected: string;
  priority_score: number;
  tier: string;
  components: PriorityComponent[];
  persistence_label: string;
  n_snapshots: number;
  model_support: boolean | null;
  lifecycle: string;
  manual_status: string | null;
}

export interface TierCount {
  tier: string;
  count: number;
}

export interface TraderInbox {
  generated_at: string;
  n_matches_scanned: number;
  total_raw_alerts: number;
  total_cases: number;
  tier_counts: TierCount[];
  cases: AnomalyCase[];
}

export function fetchCases(
  params: { tier?: string; alertType?: string; matchId?: number; bookmakerName?: string; limit?: number } = {}
): Promise<TraderInbox> {
  const qs = new URLSearchParams();
  if (params.tier) qs.set("tier", params.tier);
  if (params.alertType) qs.set("alert_type", params.alertType);
  if (params.matchId !== undefined) qs.set("match_id", String(params.matchId));
  if (params.bookmakerName) qs.set("bookmaker_name", params.bookmakerName);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return request(`/api/v1/market-monitor/cases${q ? `?${q}` : ""}`);
}

export function setCaseStatus(caseId: string, status: string | null): Promise<{ case_id: string; manual_status: string | null }> {
  return request(`/api/v1/market-monitor/cases/${encodeURIComponent(caseId)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// --- Prospective Alert Validation + Root-Cause Intelligence (effectiveness) -

export interface EffectivenessSummary {
  n_frozen_cases: number;
  n_unique_markets: number;
  n_resolved: number;
  sample_label: string;
  pct_outlier_converged: number | null;
  n_outlier_eligible: number;
  pct_consensus_moved_toward_model: number | null;
  pct_consensus_moved_away_from_model: number | null;
  pct_stale_context_repriced: number | null;
  n_stale_context_eligible: number;
  median_time_to_resolution_hours: number | null;
  pct_persisted_to_kickoff: number | null;
}

export interface AlertTypeEffectiveness {
  alert_type_family: string;
  n_resolved: number;
  sample_label: string;
  pct_market_moved_toward_model: number | null;
  pct_market_moved_away_from_model: number | null;
  pct_persisted_to_kickoff: number | null;
  pct_inconclusive: number | null;
}

export interface EffectivenessView {
  summary: EffectivenessSummary;
  by_alert_type: AlertTypeEffectiveness[];
}

export interface ProspectiveCoverage {
  n_upcoming_matches_monitored: number;
  n_frozen_cases: number;
  n_cases_with_2plus_followups: number;
  n_cases_with_3plus_followups: number;
  earliest_hours_before_kickoff_captured: number | null;
  latest_pre_kickoff_capture_hours: number | null;
}

export interface ResearchCategorySummary {
  n_tagged: number;
  n_resolved: number;
  sample_label: string;
  n_converged: number;
  n_persisted: number;
  pct_converged: number | null;
  pct_persisted: number | null;
}

export interface EffectivenessDashboard {
  generated_at: string;
  coverage: ProspectiveCoverage;
  prospective: EffectivenessView;
  retrospective: EffectivenessView;
  research_category: ResearchCategorySummary;
}

export function fetchEffectiveness(): Promise<EffectivenessDashboard> {
  return request("/api/v1/market-monitor/effectiveness");
}
