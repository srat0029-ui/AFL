"""app.config.validate_production_settings must fail loudly at startup
rather than let a production deployment silently run against the SQLite
dev database or the local-dev CORS allowlist - the two unsafe defaults
that are fine for local development but a real misconfiguration in
production."""

import pytest

from app.config import Settings, validate_production_settings


def test_local_env_is_never_validated():
    validate_production_settings(Settings(app_env="local"))  # must not raise


def test_production_with_real_postgres_and_cors_passes():
    settings = Settings(
        app_env="production",
        database_url="postgresql+psycopg://user:pass@host:5432/afl",
        cors_origins="https://afl-analytics.example.com",
    )
    validate_production_settings(settings)  # must not raise


def test_production_with_sqlite_default_is_rejected():
    settings = Settings(app_env="production", cors_origins="https://afl-analytics.example.com")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        validate_production_settings(settings)


def test_production_with_local_dev_cors_default_is_rejected():
    settings = Settings(app_env="production", database_url="postgresql+psycopg://user:pass@host:5432/afl")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        validate_production_settings(settings)


def test_production_with_both_defaults_reports_both_problems():
    settings = Settings(app_env="production")
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_settings(settings)
    assert "DATABASE_URL" in str(exc_info.value)
    assert "CORS_ORIGINS" in str(exc_info.value)
