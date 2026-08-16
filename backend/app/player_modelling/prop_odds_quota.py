"""API-quota protection for automated player-prop odds refresh (Section 10
of the automated-odds stage brief). The Odds API is usage-metered (see
app/providers/afl/the_odds_api.py's module docstring for the verified cost
model) — "Never have normal frontend page loads trigger paid external API
calls directly" and "prevention of accidental repeated fetch loops" are
both satisfied structurally (the frontend only ever reads our database;
external fetches only happen via an explicit refresh-prop-odds CLI run),
but a human re-running that CLI moments after the last run — or a future
scheduler firing more often than intended — still shouldn't re-spend quota
for data that can't meaningfully have changed yet. This module is that
gate.

Deliberately reuses already-persisted data (the newest PlayerPropMarket
row's recorded_at for a match) as the "when did we last fetch this event"
signal, rather than a separate refresh-log table — one less thing to keep
in sync, and it's already exactly the right timestamp (our own fetch time,
not the bookmaker's).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PlayerPropMarket

# Deliberately not configurable via an env var (yet) - a stage-appropriate
# default a human can override per-invocation via the CLI's --min-interval-
# minutes flag (see app/player_modelling/cli.py) covers "readiness for a
# future scheduler" (Section 27: "every few hours early in the week, more
# frequently near games") without committing to a specific schedule now.
DEFAULT_MIN_REFRESH_INTERVAL = timedelta(minutes=30)


def event_needs_refresh(
    db: Session, match_id: int, provider: str, min_interval: timedelta = DEFAULT_MIN_REFRESH_INTERVAL
) -> bool:
    """False if this match already has a provider-sourced quote fetched
    more recently than min_interval ago - the caller should skip spending
    quota on it. Always True for a match with no prior automated quote at
    all (nothing to protect yet)."""
    latest = db.scalar(
        select(func.max(PlayerPropMarket.recorded_at)).where(
            PlayerPropMarket.match_id == match_id, PlayerPropMarket.source == provider
        )
    )
    if latest is None:
        return True
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - latest >= min_interval
