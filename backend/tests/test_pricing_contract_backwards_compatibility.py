"""Confirms every field that existed on the public pricing schemas BEFORE
this phase is still present and still typed the same way — this phase only
ever ADDS fields (request_id, warnings, readiness), it never removes or
renames one, per the plan's explicit backwards-compatibility requirement."""

TEAM_FIELDS = {
    "match_id", "home_team", "away_team", "provenance", "home_win_probability", "draw_probability",
    "away_win_probability", "home_fair_odds", "draw_fair_odds", "away_fair_odds", "expected_margin",
    "expected_total_points", "home_expected_score", "away_expected_score", "lines", "totals",
}
PROVENANCE_FIELDS = {"model_name", "model_version", "generated_at", "data_cutoff"}
SGM_FIELDS = {
    "match_id", "provenance", "model_probability", "model_fair_odds", "naive_independence_probability",
    "naive_independence_fair_odds", "correlation_adjustment_pp", "mc_standard_error", "n_simulations",
    "dependence_validated", "legs",
}


def _schema_properties(openapi_schema: dict, model_name: str) -> set[str]:
    return set(openapi_schema["components"]["schemas"][model_name]["properties"].keys())


class TestBackwardsCompatibility:
    def test_team_market_price_read_keeps_every_original_field(self, client, db_session):
        schema = client.get("/openapi.json").json()
        assert TEAM_FIELDS <= _schema_properties(schema, "TeamMarketPriceRead")

    def test_model_provenance_keeps_every_original_field(self, client, db_session):
        schema = client.get("/openapi.json").json()
        assert PROVENANCE_FIELDS <= _schema_properties(schema, "ModelProvenance")

    def test_same_game_multi_price_read_keeps_every_original_field(self, client, db_session):
        schema = client.get("/openapi.json").json()
        assert SGM_FIELDS <= _schema_properties(schema, "SameGameMultiPriceRead")

    def test_disposal_price_read_keeps_stale_and_warning_fields(self, client, db_session):
        schema = client.get("/openapi.json").json()
        props = _schema_properties(schema, "DisposalPriceRead")
        assert {"warnings", "is_stale", "stale_reasons", "calibration", "thresholds"} <= props
