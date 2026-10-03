from datetime import date, datetime

from pydantic import BaseModel


class NbaDatasetStatusRead(BaseModel):
    key: str
    label: str
    rows: int
    latest_at: datetime | None


class NbaAvailabilityObservationRead(BaseModel):
    is_listed: bool
    source_status: str | None
    status: str | None
    injury_type: str | None
    injury_location: str | None
    injury_side: str | None
    injury_detail: str | None
    fantasy_status: str | None
    expected_return_date: date | None
    short_comment: str | None
    team_id: int | None
    source: str
    source_published_at: datetime | None
    observed_at: datetime


class NbaAvailabilityAsOfRead(BaseModel):
    """What was known about one player's availability at `as_of`.
    `observation` null means the injury feed had never listed the player
    while it was being watched - no evidence, which is not the same as
    available. `last_confirmed_at` is the latest read of the feed at or
    before `as_of`."""

    player_id: int
    player_name: str
    source_player_id: str
    as_of: datetime
    observation: NbaAvailabilityObservationRead | None
    first_observed_at: datetime | None
    last_confirmed_at: datetime | None


class NbaStatusRead(BaseModel):
    sport: str
    markets: list[str]
    datasets: list[NbaDatasetStatusRead]
    predictions_frozen: int
    predictions_with_closing_line: int
    predictions_settled: int
