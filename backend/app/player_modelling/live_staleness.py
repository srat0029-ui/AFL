"""Stale-projection detection — Section 5 of the live-projection brief.
A projection can go stale along four independent axes; this reports which
of them, if any, have moved since the projection was generated, rather than
a single opaque "stale: true/false".
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StalenessCheck:
    is_stale: bool
    reasons: list[str]


def check_staleness(
    *,
    projection_model_version: str,
    projection_data_cutoff: datetime,
    projection_lineup_status: str,
    current_model_version: str | None,
    current_data_cutoff: datetime | None,
    current_lineup_status: str | None,
    projection_team_model_version: str | None = None,
    current_team_model_version: str | None = None,
    projection_generated_at: datetime | None = None,
    latest_context_at: datetime | None = None,
) -> StalenessCheck:
    reasons: list[str] = []

    if current_model_version is not None and projection_model_version != current_model_version:
        reasons.append("The underlying model has been retrained since this projection was generated.")

    if current_data_cutoff is not None and projection_data_cutoff < current_data_cutoff:
        reasons.append("More recent match results are available than this projection used.")

    # A 4th, independent axis (team-selection stage, Section 6): the Elo/
    # Poisson team-context CONFIG changed (e.g. re-tuned and re-promoted)
    # without necessarily any new completed match being added — distinct
    # from current_data_cutoff moving forward, which is a NEW-RESULTS
    # change, not a config change.
    if (
        current_team_model_version is not None
        and projection_team_model_version is not None
        and projection_team_model_version != current_team_model_version
    ):
        reasons.append("The team Elo/Poisson context has been re-tuned since this projection was generated.")

    if current_lineup_status is None:
        reasons.append("This player's expected-lineup record has been removed since this projection was generated.")
    elif projection_lineup_status != current_lineup_status:
        reasons.append(
            f"Lineup status has changed since generation (was {projection_lineup_status!r}, now {current_lineup_status!r})."
        )

    # A 5th, independent axis (Current Context + Team News Intelligence
    # stage, Sections 7, 13): a current-context item (injury, substitute,
    # returning-player, etc.) for this player was recorded/reported AFTER
    # this projection was generated — the projection's own lineup-status
    # snapshot may not have moved yet even though newer context exists.
    if projection_generated_at is not None and latest_context_at is not None:
        from datetime import timezone as _tz

        gen = projection_generated_at if projection_generated_at.tzinfo is not None else projection_generated_at.replace(tzinfo=_tz.utc)
        ctx = latest_context_at if latest_context_at.tzinfo is not None else latest_context_at.replace(tzinfo=_tz.utc)
        if ctx > gen:
            reasons.append("Newer team/player context is available than this projection used.")

    return StalenessCheck(is_stale=bool(reasons), reasons=reasons)
