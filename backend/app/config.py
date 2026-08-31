"""Application configuration, loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./afl.db"
    app_env: str = "local"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    # Squiggle's usage policy requires a descriptive User-Agent identifying the
    # client and a contact email. See https://api.squiggle.com.au/
    squiggle_user_agent: str = "AFL Analytics Prototype (personal project) - samvrathore@gmail.com"

    # The Odds API (https://the-odds-api.com) key for automated player-prop
    # odds ingestion. Empty string, not None, so `if not settings.the_odds_api_key`
    # is the one check every caller needs - no separate "is it configured"
    # branch. Absence must never crash the app: automated refresh reports the
    # provider as unavailable and manual prop entry keeps working unaffected.
    the_odds_api_key: str = ""

    # How often the automatic scheduler (app/player_modelling/scheduler.py)
    # calls run_live_cycle when left running unattended. This just paces how
    # often the free Squiggle/AFL Tables scrapers get polled - it does NOT
    # affect paid-odds-API request frequency, which run_live_cycle already
    # gates internally via its own match-time-aware refresh interval
    # (prop_odds_quota.py) regardless of how often it's called.
    live_cycle_interval_minutes: int = 15

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


_LOCAL_DEV_CORS_DEFAULT = "http://localhost:5173,http://127.0.0.1:5173"


def validate_production_settings(settings: Settings) -> None:
    """Fails loudly at startup rather than silently running a production
    deployment against the SQLite dev database or the local-dev CORS
    allowlist - the two defaults that are safe for local development but
    would be a real, silent misconfiguration in production. Every other
    setting (e.g. THE_ODDS_API_KEY) already has a supported, tested
    "absent" behavior and is deliberately not required here."""
    if settings.app_env != "production":
        return
    problems = []
    if settings.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL is still the SQLite default - set it to a real Postgres URL for production.")
    if settings.cors_origins == _LOCAL_DEV_CORS_DEFAULT:
        problems.append("CORS_ORIGINS is still the local-dev default - set it to the real deployed frontend origin(s).")
    if problems:
        raise RuntimeError("Refusing to start with APP_ENV=production and unsafe defaults:\n- " + "\n- ".join(problems))
