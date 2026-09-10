"""Tag-watch: whether a player is likely to draw a defensive tag, and what
that would do to their expected output.

AFL Tables-style box-score data alone cannot establish who tagged whom or
for how long - that fact isn't published in structured form anywhere this
project ingests from (see app/models/player_tag_annotation.py's docstring
for the repo-wide search that confirmed this). This module never
fabricates a tag probability from possession/tackle counts or any other
proxy; it only ever reports a real rate once enough genuinely VERIFIED
`PlayerTagAnnotation` rows exist for a player, and reports
"insufficient_verified_data" otherwise - which today means always, since
no ingestion pipeline populates that table yet. See
docs (Player Context Research direction) for why this is a foundation,
not a finished feature.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.player_match_stat import PlayerMatchStat
from app.models.player_tag_annotation import PlayerTagAnnotation

# Below this many verified annotations, a rate would be estimated from too
# few real observations to report at all.
MIN_VERIFIED_TAG_ANNOTATIONS = 5


class TagWatchStatus(str, Enum):
    INSUFFICIENT_VERIFIED_DATA = "insufficient_verified_data"
    AVAILABLE = "available"


@dataclass(frozen=True)
class TagWatchResult:
    status: TagWatchStatus
    verified_annotation_count: int
    games_played: int | None
    tag_rate: float | None
    explanation: str


def tag_watch_for_player(db: Session, player_id: int) -> TagWatchResult:
    annotation_count = len(
        db.scalars(select(PlayerTagAnnotation.id).where(PlayerTagAnnotation.tagged_player_id == player_id)).all()
    )

    if annotation_count < MIN_VERIFIED_TAG_ANNOTATIONS:
        return TagWatchResult(
            status=TagWatchStatus.INSUFFICIENT_VERIFIED_DATA,
            verified_annotation_count=annotation_count,
            games_played=None,
            tag_rate=None,
            explanation=(
                "AFL Tables-style box-score data cannot reliably establish who was tagged or for how "
                "long, so this is never inferred from possession/tackle counts. A real tag rate is only "
                f"reported once at least {MIN_VERIFIED_TAG_ANNOTATIONS} verified tag annotations exist "
                f"for this player (found {annotation_count})."
            ),
        )

    games_played = len(db.scalars(select(PlayerMatchStat.id).where(PlayerMatchStat.player_id == player_id)).all())
    tag_rate = annotation_count / games_played if games_played else None
    return TagWatchResult(
        status=TagWatchStatus.AVAILABLE,
        verified_annotation_count=annotation_count,
        games_played=games_played,
        tag_rate=tag_rate,
        explanation=f"Based on {annotation_count} verified tag annotations across {games_played} recorded games.",
    )


def tag_watch_as_dict(result: TagWatchResult) -> dict:
    return {
        "status": result.status.value,
        "verified_annotation_count": result.verified_annotation_count,
        "games_played": result.games_played,
        "tag_rate": result.tag_rate,
        "explanation": result.explanation,
    }
