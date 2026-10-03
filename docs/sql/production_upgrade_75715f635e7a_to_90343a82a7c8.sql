BEGIN;

-- Running upgrade 75715f635e7a -> 04b59a8a329c

CREATE TABLE player_tag_annotations (
    id SERIAL NOT NULL, 
    match_id INTEGER NOT NULL, 
    tagged_player_id INTEGER NOT NULL, 
    tagger_player_id INTEGER, 
    confidence VARCHAR(32) NOT NULL, 
    source VARCHAR(64) NOT NULL, 
    tagged_duration_pct FLOAT, 
    notes TEXT, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(match_id) REFERENCES matches (id), 
    FOREIGN KEY(tagged_player_id) REFERENCES players (id), 
    FOREIGN KEY(tagger_player_id) REFERENCES players (id)
);

CREATE INDEX ix_player_tag_annotations_match_id ON player_tag_annotations (match_id);

CREATE INDEX ix_player_tag_annotations_tagged_player_id ON player_tag_annotations (tagged_player_id);

CREATE INDEX ix_player_tag_annotations_tagger_player_id ON player_tag_annotations (tagger_player_id);

UPDATE alembic_version SET version_num='04b59a8a329c' WHERE alembic_version.version_num = '75715f635e7a';

-- Running upgrade 04b59a8a329c -> 1e453acf52cb

CREATE TABLE nba_teams (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    abbreviation VARCHAR(8) NOT NULL, 
    external_ids JSON, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_nba_team_name UNIQUE (name)
);

CREATE INDEX ix_nba_teams_abbreviation ON nba_teams (abbreviation);

CREATE TABLE nba_games (
    id SERIAL NOT NULL, 
    season_start_year INTEGER NOT NULL, 
    season_type VARCHAR(16) NOT NULL, 
    game_date DATE NOT NULL, 
    scheduled_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    home_team_id INTEGER NOT NULL, 
    away_team_id INTEGER NOT NULL, 
    home_score INTEGER, 
    away_score INTEGER, 
    source VARCHAR(32) NOT NULL, 
    source_game_id VARCHAR(64) NOT NULL, 
    external_ids JSON, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_nba_game_distinct_teams CHECK (home_team_id != away_team_id), 
    FOREIGN KEY(away_team_id) REFERENCES nba_teams (id), 
    FOREIGN KEY(home_team_id) REFERENCES nba_teams (id), 
    CONSTRAINT uq_nba_game_source_id UNIQUE (source, source_game_id)
);

CREATE INDEX ix_nba_games_away_team_id ON nba_games (away_team_id);

CREATE INDEX ix_nba_games_game_date ON nba_games (game_date);

CREATE INDEX ix_nba_games_home_team_id ON nba_games (home_team_id);

CREATE INDEX ix_nba_games_scheduled_start ON nba_games (scheduled_start);

CREATE INDEX ix_nba_games_season_start_year ON nba_games (season_start_year);

CREATE INDEX ix_nba_games_status ON nba_games (status);

CREATE TABLE nba_players (
    id SERIAL NOT NULL, 
    display_name VARCHAR(128) NOT NULL, 
    current_team_id INTEGER, 
    position VARCHAR(8), 
    source VARCHAR(32) NOT NULL, 
    source_player_id VARCHAR(64) NOT NULL, 
    source_metadata JSON, 
    is_active BOOLEAN, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(current_team_id) REFERENCES nba_teams (id), 
    CONSTRAINT uq_nba_player_source_id UNIQUE (source, source_player_id)
);

CREATE INDEX ix_nba_players_current_team_id ON nba_players (current_team_id);

CREATE INDEX ix_nba_players_display_name ON nba_players (display_name);

CREATE TABLE nba_player_availability_reports (
    id SERIAL NOT NULL, 
    player_id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    game_id INTEGER, 
    status VARCHAR(16) NOT NULL, 
    reason VARCHAR(200), 
    source VARCHAR(32) NOT NULL, 
    source_published_at TIMESTAMP WITH TIME ZONE, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(player_id) REFERENCES nba_players (id), 
    FOREIGN KEY(team_id) REFERENCES nba_teams (id)
);

CREATE INDEX ix_nba_player_availability_reports_game_id ON nba_player_availability_reports (game_id);

CREATE INDEX ix_nba_player_availability_reports_observed_at ON nba_player_availability_reports (observed_at);

CREATE INDEX ix_nba_player_availability_reports_player_id ON nba_player_availability_reports (player_id);

CREATE INDEX ix_nba_player_availability_reports_team_id ON nba_player_availability_reports (team_id);

CREATE TABLE nba_player_game_logs (
    id SERIAL NOT NULL, 
    player_id INTEGER NOT NULL, 
    game_id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    opponent_team_id INTEGER NOT NULL, 
    is_home BOOLEAN NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    did_not_play BOOLEAN NOT NULL, 
    did_not_play_reason VARCHAR(128), 
    started BOOLEAN, 
    minutes FLOAT, 
    points INTEGER, 
    rebounds INTEGER, 
    assists INTEGER, 
    offensive_rebounds INTEGER, 
    defensive_rebounds INTEGER, 
    field_goals_made INTEGER, 
    field_goals_attempted INTEGER, 
    three_pointers_made INTEGER, 
    three_pointers_attempted INTEGER, 
    free_throws_made INTEGER, 
    free_throws_attempted INTEGER, 
    steals INTEGER, 
    blocks INTEGER, 
    turnovers INTEGER, 
    personal_fouls INTEGER, 
    plus_minus INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(opponent_team_id) REFERENCES nba_teams (id), 
    FOREIGN KEY(player_id) REFERENCES nba_players (id), 
    FOREIGN KEY(team_id) REFERENCES nba_teams (id), 
    CONSTRAINT uq_nba_player_game_log_player_game_source UNIQUE (player_id, game_id, source)
);

CREATE INDEX ix_nba_player_game_logs_game_id ON nba_player_game_logs (game_id);

CREATE INDEX ix_nba_player_game_logs_opponent_team_id ON nba_player_game_logs (opponent_team_id);

CREATE INDEX ix_nba_player_game_logs_player_id ON nba_player_game_logs (player_id);

CREATE INDEX ix_nba_player_game_logs_team_id ON nba_player_game_logs (team_id);

CREATE TABLE nba_prop_projections (
    id SERIAL NOT NULL, 
    game_id INTEGER NOT NULL, 
    player_id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    market VARCHAR(32) NOT NULL, 
    model_name VARCHAR(64) NOT NULL, 
    model_version VARCHAR(128) NOT NULL, 
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    information_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, 
    expected_minutes FLOAT, 
    rate_per_minute FLOAT, 
    predicted_mean FLOAT NOT NULL, 
    distribution_kind VARCHAR(32) NOT NULL, 
    distribution_params JSON NOT NULL, 
    games_of_history INTEGER NOT NULL, 
    inputs JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(player_id) REFERENCES nba_players (id), 
    FOREIGN KEY(team_id) REFERENCES nba_teams (id), 
    CONSTRAINT uq_nba_prop_projection_identity UNIQUE (game_id, player_id, market, model_version, information_cutoff)
);

CREATE INDEX ix_nba_prop_projections_game_id ON nba_prop_projections (game_id);

CREATE INDEX ix_nba_prop_projections_information_cutoff ON nba_prop_projections (information_cutoff);

CREATE INDEX ix_nba_prop_projections_market ON nba_prop_projections (market);

CREATE INDEX ix_nba_prop_projections_model_version ON nba_prop_projections (model_version);

CREATE INDEX ix_nba_prop_projections_player_id ON nba_prop_projections (player_id);

CREATE INDEX ix_nba_prop_projections_team_id ON nba_prop_projections (team_id);

CREATE TABLE nba_prop_quotes (
    id SERIAL NOT NULL, 
    game_id INTEGER NOT NULL, 
    player_id INTEGER NOT NULL, 
    bookmaker_id INTEGER NOT NULL, 
    market VARCHAR(32) NOT NULL, 
    line FLOAT NOT NULL, 
    selection VARCHAR(8) NOT NULL, 
    is_alternate_line BOOLEAN NOT NULL, 
    price_decimal FLOAT NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    bookmaker_last_update TIMESTAMP WITH TIME ZONE, 
    source VARCHAR(32) NOT NULL, 
    provider_event_id VARCHAR(64), 
    provider_market_key VARCHAR(48), 
    raw_outcome JSON, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(player_id) REFERENCES nba_players (id)
);

CREATE INDEX ix_nba_prop_quotes_bookmaker_id ON nba_prop_quotes (bookmaker_id);

CREATE INDEX ix_nba_prop_quotes_game_id ON nba_prop_quotes (game_id);

CREATE INDEX ix_nba_prop_quotes_market ON nba_prop_quotes (market);

CREATE INDEX ix_nba_prop_quotes_observed_at ON nba_prop_quotes (observed_at);

CREATE INDEX ix_nba_prop_quotes_player_id ON nba_prop_quotes (player_id);

CREATE INDEX ix_nba_prop_quotes_provider_event_id ON nba_prop_quotes (provider_event_id);

CREATE TABLE nba_prop_predictions (
    id SERIAL NOT NULL, 
    projection_id INTEGER NOT NULL, 
    game_id INTEGER NOT NULL, 
    player_id INTEGER NOT NULL, 
    market VARCHAR(32) NOT NULL, 
    line FLOAT NOT NULL, 
    selection VARCHAR(8) NOT NULL, 
    model_name VARCHAR(64) NOT NULL, 
    model_version VARCHAR(128) NOT NULL, 
    information_cutoff TIMESTAMP WITH TIME ZONE NOT NULL, 
    model_probability FLOAT NOT NULL, 
    model_fair_odds FLOAT NOT NULL, 
    predicted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    tipoff_at_prediction TIMESTAMP WITH TIME ZONE NOT NULL, 
    entry_bookmaker_id INTEGER, 
    entry_quote_id INTEGER, 
    entry_price FLOAT, 
    entry_quote_observed_at TIMESTAMP WITH TIME ZONE, 
    entry_consensus_probability FLOAT, 
    entry_n_bookmakers INTEGER, 
    entry_expected_value FLOAT, 
    closing_captured_at TIMESTAMP WITH TIME ZONE, 
    closing_main_line FLOAT, 
    closing_price FLOAT, 
    closing_quote_id INTEGER, 
    closing_quote_observed_at TIMESTAMP WITH TIME ZONE, 
    closing_consensus_probability FLOAT, 
    closing_n_bookmakers INTEGER, 
    settled_at TIMESTAMP WITH TIME ZONE, 
    actual_value FLOAT, 
    outcome VARCHAR(16), 
    settlement_note VARCHAR(200), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(closing_quote_id) REFERENCES nba_prop_quotes (id), 
    FOREIGN KEY(entry_bookmaker_id) REFERENCES bookmakers (id), 
    FOREIGN KEY(entry_quote_id) REFERENCES nba_prop_quotes (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(player_id) REFERENCES nba_players (id), 
    FOREIGN KEY(projection_id) REFERENCES nba_prop_projections (id), 
    CONSTRAINT uq_nba_prop_prediction_projection_line_selection UNIQUE (projection_id, line, selection)
);

CREATE INDEX ix_nba_prop_predictions_entry_bookmaker_id ON nba_prop_predictions (entry_bookmaker_id);

CREATE INDEX ix_nba_prop_predictions_game_id ON nba_prop_predictions (game_id);

CREATE INDEX ix_nba_prop_predictions_market ON nba_prop_predictions (market);

CREATE INDEX ix_nba_prop_predictions_model_version ON nba_prop_predictions (model_version);

CREATE INDEX ix_nba_prop_predictions_outcome ON nba_prop_predictions (outcome);

CREATE INDEX ix_nba_prop_predictions_player_id ON nba_prop_predictions (player_id);

CREATE INDEX ix_nba_prop_predictions_predicted_at ON nba_prop_predictions (predicted_at);

CREATE INDEX ix_nba_prop_predictions_projection_id ON nba_prop_predictions (projection_id);

UPDATE alembic_version SET version_num='1e453acf52cb' WHERE alembic_version.version_num = '04b59a8a329c';

-- Running upgrade 1e453acf52cb -> becc7fa40ce6

CREATE TABLE nba_schedule_sync_dates (
    id SERIAL NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    game_date DATE NOT NULL, 
    synced_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    games_kept INTEGER NOT NULL, 
    is_settled BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_nba_schedule_sync_date_source_date UNIQUE (source, game_date)
);

CREATE INDEX ix_nba_schedule_sync_dates_game_date ON nba_schedule_sync_dates (game_date);

ALTER TABLE nba_games ADD COLUMN source_status VARCHAR(48);

ALTER TABLE nba_games ADD COLUMN source_season_type VARCHAR(48);

ALTER TABLE nba_games ADD COLUMN source_synced_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE nba_games ADD COLUMN box_score_state VARCHAR(24);

ALTER TABLE nba_games ADD COLUMN box_score_synced_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_nba_games_box_score_state ON nba_games (box_score_state);

ALTER TABLE nba_player_availability_reports ADD COLUMN source_status VARCHAR(48) NOT NULL;

ALTER TABLE nba_player_availability_reports ALTER COLUMN status DROP NOT NULL;

ALTER TABLE nba_players ADD COLUMN last_game_at TIMESTAMP WITH TIME ZONE;

UPDATE alembic_version SET version_num='becc7fa40ce6' WHERE alembic_version.version_num = '1e453acf52cb';

-- Running upgrade becc7fa40ce6 -> 90343a82a7c8

CREATE TABLE nba_evidence_polls (
    id SERIAL NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    scope VARCHAR(64), 
    source VARCHAR(32) NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    source_timestamp TIMESTAMP WITH TIME ZONE, 
    items_seen INTEGER NOT NULL, 
    observations_added INTEGER NOT NULL, 
    payload_sha256 VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_nba_evidence_polls_kind ON nba_evidence_polls (kind);

CREATE INDEX ix_nba_evidence_polls_observed_at ON nba_evidence_polls (observed_at);

CREATE INDEX ix_nba_evidence_polls_scope ON nba_evidence_polls (scope);

CREATE TABLE nba_live_cycle_runs (
    id SERIAL NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    status VARCHAR(16) NOT NULL, 
    steps JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_nba_live_cycle_runs_started_at ON nba_live_cycle_runs (started_at);

CREATE TABLE nba_team_observations (
    id SERIAL NOT NULL, 
    poll_id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    source_timestamp TIMESTAMP WITH TIME ZONE, 
    payload JSON NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(poll_id) REFERENCES nba_evidence_polls (id), 
    FOREIGN KEY(team_id) REFERENCES nba_teams (id)
);

CREATE INDEX ix_nba_team_observations_kind ON nba_team_observations (kind);

CREATE INDEX ix_nba_team_observations_observed_at ON nba_team_observations (observed_at);

CREATE INDEX ix_nba_team_observations_poll_id ON nba_team_observations (poll_id);

CREATE INDEX ix_nba_team_observations_team_id ON nba_team_observations (team_id);

CREATE TABLE nba_game_lineup_observations (
    id SERIAL NOT NULL, 
    poll_id INTEGER NOT NULL, 
    game_id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    tipoff_at_observation TIMESTAMP WITH TIME ZONE NOT NULL, 
    game_status_at_observation VARCHAR(16) NOT NULL, 
    lineup_available BOOLEAN, 
    has_starter_field BOOLEAN NOT NULL, 
    starters_flagged INTEGER NOT NULL, 
    players_listed INTEGER NOT NULL, 
    payload JSON NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id), 
    FOREIGN KEY(poll_id) REFERENCES nba_evidence_polls (id), 
    FOREIGN KEY(team_id) REFERENCES nba_teams (id)
);

CREATE INDEX ix_nba_game_lineup_observations_game_id ON nba_game_lineup_observations (game_id);

CREATE INDEX ix_nba_game_lineup_observations_observed_at ON nba_game_lineup_observations (observed_at);

CREATE INDEX ix_nba_game_lineup_observations_poll_id ON nba_game_lineup_observations (poll_id);

CREATE INDEX ix_nba_game_lineup_observations_team_id ON nba_game_lineup_observations (team_id);

CREATE TABLE nba_game_schedule_observations (
    id SERIAL NOT NULL, 
    game_id INTEGER NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    source_status VARCHAR(48), 
    scheduled_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    game_date DATE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(game_id) REFERENCES nba_games (id)
);

CREATE INDEX ix_nba_game_schedule_observations_game_id ON nba_game_schedule_observations (game_id);

CREATE INDEX ix_nba_game_schedule_observations_observed_at ON nba_game_schedule_observations (observed_at);

ALTER TABLE nba_player_availability_reports ADD COLUMN poll_id INTEGER;

ALTER TABLE nba_player_availability_reports ADD COLUMN is_listed BOOLEAN DEFAULT true NOT NULL;

ALTER TABLE nba_player_availability_reports ADD COLUMN source_status_type VARCHAR(48);

ALTER TABLE nba_player_availability_reports ADD COLUMN injury_type VARCHAR(64);

ALTER TABLE nba_player_availability_reports ADD COLUMN injury_location VARCHAR(64);

ALTER TABLE nba_player_availability_reports ADD COLUMN injury_side VARCHAR(32);

ALTER TABLE nba_player_availability_reports ADD COLUMN injury_detail VARCHAR(128);

ALTER TABLE nba_player_availability_reports ADD COLUMN fantasy_status VARCHAR(16);

ALTER TABLE nba_player_availability_reports ADD COLUMN expected_return_date DATE;

ALTER TABLE nba_player_availability_reports ADD COLUMN short_comment TEXT;

ALTER TABLE nba_player_availability_reports ADD COLUMN long_comment TEXT;

ALTER TABLE nba_player_availability_reports ADD COLUMN source_report_id VARCHAR(32);

ALTER TABLE nba_player_availability_reports ADD COLUMN source_team_id VARCHAR(16);

ALTER TABLE nba_player_availability_reports ADD COLUMN source_athlete_team_id VARCHAR(16);

ALTER TABLE nba_player_availability_reports ADD COLUMN content_hash VARCHAR(64);

ALTER TABLE nba_player_availability_reports ADD COLUMN raw JSON;

ALTER TABLE nba_player_availability_reports ALTER COLUMN team_id DROP NOT NULL;

ALTER TABLE nba_player_availability_reports ALTER COLUMN source_status DROP NOT NULL;

CREATE INDEX ix_nba_player_availability_reports_poll_id ON nba_player_availability_reports (poll_id);

ALTER TABLE nba_player_availability_reports ADD CONSTRAINT fk_nba_player_availability_reports_poll_id FOREIGN KEY(poll_id) REFERENCES nba_evidence_polls (id);

ALTER TABLE nba_player_availability_reports DROP COLUMN reason;

UPDATE alembic_version SET version_num='90343a82a7c8' WHERE alembic_version.version_num = 'becc7fa40ce6';

COMMIT;

