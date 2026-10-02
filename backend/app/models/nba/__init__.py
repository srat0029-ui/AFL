"""NBA's own tables. Imported by app/models/__init__.py so Base.metadata
(Alembic autogenerate, create_all() in tests) sees them.

NBA does NOT store its games/players/teams as extra rows in AFL's
`matches`/`players`/`teams` tables, even though those carry a `sport_id`.
Two reasons, both verified against the code rather than assumed:

1. Most AFL query paths do not filter by sport — including the ones that
   sweep every SCHEDULED match into pricing snapshots and the market
   monitor. NBA rows in `matches` would flow straight into AFL pricing.
2. The shapes genuinely differ: `Match` requires a round and carries
   goals/behinds; `PlayerMatchStat` is AFL's stat columns. An NBA game log
   is minutes, points, rebounds, assists.

What IS shared at the table level is `bookmakers` (a bookmaker is the same
company whichever sport it prices). See docs/MULTI_SPORT_ARCHITECTURE.md.

Rows fall into three integrity classes (enforced below via
app/core/prospective.py's protect_frozen_record):

- Reference/result data, corrected in place: teams, players, games, game logs.
- Append-only observations of the outside world, never edited: availability
  reports, prop quotes. Each carries `observed_at` — when THIS system
  learned it — which is what the as-of boundary filters on.
- Frozen model output: projections (fully frozen) and predictions (frozen
  at entry; the closing-line group and the settlement group are each
  written exactly once afterwards).
"""

from app.core.prospective import protect_frozen_record
from app.models.nba.game import NbaGame, NbaGameStatus, NbaSeasonType
from app.models.nba.player import NbaPlayer
from app.models.nba.player_availability_report import NbaAvailabilityStatus, NbaPlayerAvailabilityReport
from app.models.nba.player_game_log import NbaPlayerGameLog
from app.models.nba.prop_prediction import CLOSING_FIELDS, SETTLEMENT_FIELDS, NbaPropPrediction
from app.models.nba.prop_projection import NbaPropProjection
from app.models.nba.prop_quote import NbaPropQuote
from app.models.nba.team import NbaTeam

protect_frozen_record(NbaPlayerAvailabilityReport)
protect_frozen_record(NbaPropQuote)
protect_frozen_record(NbaPropProjection)
protect_frozen_record(
    NbaPropPrediction,
    write_once_groups={"closing_captured_at": CLOSING_FIELDS, "settled_at": SETTLEMENT_FIELDS},
)

__all__ = [
    "NbaTeam",
    "NbaPlayer",
    "NbaGame",
    "NbaGameStatus",
    "NbaSeasonType",
    "NbaPlayerGameLog",
    "NbaPlayerAvailabilityReport",
    "NbaAvailabilityStatus",
    "NbaPropQuote",
    "NbaPropProjection",
    "NbaPropPrediction",
]
