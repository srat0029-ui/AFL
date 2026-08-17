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

from dataclasses import dataclass
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


# --- Match-time-aware refresh policy (live-operations stage, Section 4) ----
#
# "Not every game needs odds refreshed equally often" - a match 6 days out
# is exceedingly unlikely to have a meaningfully different price than it
# did an hour ago, while a match kicking off soon is where fresh data
# actually matters most. Bands are (min_hours_before_kickoff (inclusive),
# max_hours_before_kickoff (exclusive), interval) checked in order;
# DEFAULT_MIN_REFRESH_INTERVAL (30 min) remains the tightest band's floor
# deliberately - Section 4 is explicit that "the actual fetch command must
# continue respecting quota safeguards," so even the <1h band doesn't get
# more aggressive than the interval this project already validated against
# The Odds API's real usage-metered cost model (see
# app/providers/afl/the_odds_api.py). A caller that wants a DIFFERENT
# schedule passes its own `bands` list - nothing here is hardcoded into
# run_prop_odds_refresh itself beyond this default.


@dataclass(frozen=True)
class RefreshBand:
    min_hours: float  # inclusive
    max_hours: float  # exclusive (use float('inf') for the open-ended top band)
    interval: timedelta


DEFAULT_REFRESH_BANDS: list[RefreshBand] = [
    RefreshBand(72.0, float("inf"), timedelta(hours=24)),  # >72h: very infrequent
    RefreshBand(24.0, 72.0, timedelta(hours=6)),  # 24-72h: occasional
    RefreshBand(6.0, 24.0, timedelta(hours=2)),  # 6-24h: more frequent
    RefreshBand(1.0, 6.0, timedelta(hours=1)),  # 1-6h: more frequent again
    RefreshBand(0.0, 1.0, DEFAULT_MIN_REFRESH_INTERVAL),  # <1h: highest useful frequency our quota safely permits
]


def recommended_refresh_interval(hours_to_kickoff: float, bands: list[RefreshBand] = DEFAULT_REFRESH_BANDS) -> timedelta:
    """The minimum time that should elapse between automated refreshes for
    a match this many hours from bounce. A negative hours_to_kickoff (match
    already started) falls through to the tightest band - a live/in-play
    market, if a provider ever returns one, shouldn't be throttled looser
    than the pre-match floor. Falls back to DEFAULT_MIN_REFRESH_INTERVAL if
    somehow no band matches (bands list customized to not cover every case)."""
    for band in bands:
        if band.min_hours <= hours_to_kickoff < band.max_hours or (hours_to_kickoff < 0 and band.min_hours == 0.0):
            return band.interval
    return DEFAULT_MIN_REFRESH_INTERVAL


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
