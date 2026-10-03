"""Application configuration, loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(value: str) -> str:
    """Managed Postgres providers (Layerbase included) issue the standard
    `postgresql://` scheme, which SQLAlchemy maps to psycopg2 by default -
    not installed here, since this project standardized on Psycopg 3 (see
    requirements.txt). Normalizing the scheme once, centrally, means a real
    provider's connection string works exactly as issued, with no manual
    `+psycopg` rewrite required at deploy time. Only the bare
    `postgresql://` prefix is touched - an already explicit driver
    (`postgresql+psycopg://`, or any other), SQLite URLs, and every query
    parameter (`sslmode`, custom ports, etc.) pass through completely
    unchanged. Shared by Settings (the running application) and
    scripts/seed_production.py (which takes a target URL directly on the
    command line, bypassing Settings entirely) - the same bug is possible
    in both places, so both call this one function rather than each
    re-implementing the check."""
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./afl.db"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)
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

    # NBA live evidence cycle (app/nba/live_cycle.py). Each interval is the
    # MINIMUM time between two polls of that kind: the cycle can be woken as
    # often as you like (the hosted schedule wakes it every 15 minutes) and a
    # step that is not yet due is skipped without a request. These settings
    # are the only place polling frequency is defined; every one can be
    # overridden by an environment variable of the same name in capitals.
    nba_poll_availability_minutes: int = 30
    nba_poll_schedule_minutes: int = 180
    nba_poll_team_rosters_minutes: int = 1440
    nba_poll_depth_charts_minutes: int = 1440
    nba_poll_box_scores_minutes: int = 60
    # Schedule window refreshed each time, in days either side of today.
    nba_schedule_lookback_days: int = 3
    nba_schedule_lookahead_days: int = 14
    # Pregame lineup polling tightens as tip-off approaches, to find out when
    # the source starts publishing starters. Comma-separated
    # "<hours before tip-off>:<minutes between polls>" tiers: a game is
    # polled at the interval of the smallest tier its time-to-tip falls
    # within. Further out than the largest tier: not polled. After tip-off:
    # not polled. Default: 4-24h hourly, 1-4h every 30 min, under 1h every
    # 15 min.
    nba_lineup_poll_tiers: str = "24:60,4:30,1:15"
    # A live-cycle run still marked in progress after this long is taken to
    # have died (the hosted job's own timeout is shorter).
    nba_live_cycle_stale_after_minutes: int = 40
    # Monitoring: a kind of evidence is reported stale when its last
    # successful poll is older than this many times its polling interval.
    nba_monitor_stale_multiplier: float = 3.0
    # Seconds between requests to the NBA stats source.
    nba_request_interval_seconds: float = 0.5

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
