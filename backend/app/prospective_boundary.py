"""The formal boundary for genuine post-fix prospective evidence in
production: PRODUCTION_PROSPECTIVE_TRACKING_START.

This is a fixed historical fact about production - live_cycle_runs.id=7's
run_at, the first fully-completed (`overall_status="ok"`) production Live
Cycle run after the 2026 Wildcard Final player-stat fix - never a
per-environment setting, so it is deliberately a source-controlled
constant, not an environment variable. A missing or misconfigured
environment variable could silently produce "no boundary" in production;
a literal constant shipped in the same commit as the filtering code that
depends on it cannot be missing.

Local/dev/test environments intentionally see the full, unfiltered
history (`prospective_tracking_start()` returns None there) so existing
developer tooling and historical test fixtures keep working unchanged.

Formal production reporting code must call `prospective_tracking_start()`
exactly once per request and thread the result down explicitly to the
query functions it calls - never re-derive it independently in more than
one place - so every formal report is guaranteed to use the identical
boundary. This module intentionally does nothing else: no generalized
configuration framework, no database table, no migration.
"""

from datetime import datetime, timezone

from app.config import Settings, get_settings

PRODUCTION_PROSPECTIVE_TRACKING_START = datetime(2026, 9, 10, 8, 32, 51, 944004, tzinfo=timezone.utc)


def prospective_tracking_start(settings: Settings | None = None) -> datetime | None:
    """The formal prospective-evidence boundary for the current environment.

    Production always returns the fixed, exact boundary above - there is
    no code path where a missing environment variable could silently
    disable it, since nothing here reads the environment for the VALUE
    itself, only for which environment this is. Every other environment
    (local dev, tests, CI) returns None, meaning "no boundary - report the
    full history," matching behavior before this boundary existed.
    """
    settings = settings or get_settings()
    if settings.app_env != "production":
        return None
    return PRODUCTION_PROSPECTIVE_TRACKING_START
