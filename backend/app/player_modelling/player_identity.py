"""Safe workflows for two related identity problems the first live prop
audit actually surfaced (live-operations stage brief, Sections 8-9):

1. A genuinely new 2026 debutant with no AFL Tables history yet — the
   historical backfill (2016-2025) obviously can't contain them, so without
   an explicit "add this player" workflow, a live bookmaker quote for them
   would forever resolve as "unresolved" and their real market data would
   be silently discarded (Section 8 explicitly forbids that: "Do not
   discard their bookmaker markets merely because they debuted after the
   historical dataset ends").
2. A verified real-world name spelling a provider uses that doesn't match
   our display_name and isn't covered by the existing nickname table (see
   prop_player_resolution.py) — handled by the small PlayerAlias table this
   module also manages (Section 9).

Both are deliberately manual, explicit, human-confirmed actions — this
module never guesses that a name is "probably" a new player or "probably"
an alias; see create_new_player's duplicate-name safety check and the
alias functions' exact-match-only behaviour.
"""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player, PlayerAlias, Team

NEW_PLAYER_SOURCE_PREFIX = "manual_2026"


@dataclass(frozen=True)
class DuplicateNameWarning:
    existing_player_id: int
    existing_player_source: str
    existing_player_team_id: int | None


def create_new_player(
    db: Session, *, display_name: str, team_id: int, note: str | None = None, force: bool = False
) -> Player | DuplicateNameWarning:
    """Creates a Player row with ZERO historical games — no PlayerMatchStat
    rows are fabricated or backfilled; the player's confidence tier will
    naturally read as insufficient_history/lower_confidence through the
    existing games-of-history-driven confidence logic (see
    disposal_confidence.py/goal_confidence.py) purely because no history
    rows exist, not because of any special-casing here.

    Guards against accidentally creating a duplicate of a player who
    already exists (possibly under a different team, e.g. a trade, or
    literally the same name as a historical player — AFL has had real
    same-name cases) by returning a DuplicateNameWarning instead of
    creating anything when an existing Player already has this exact
    display_name — UNLESS force=True, since a genuine same-name debutant
    is possible and must not be permanently blocked.
    """
    team = db.get(Team, team_id)
    if team is None:
        raise ValueError(f"Team {team_id} does not exist")

    if not force:
        existing = db.scalar(select(Player).where(func.lower(Player.display_name) == display_name.strip().lower()))
        if existing is not None:
            return DuplicateNameWarning(
                existing_player_id=existing.id, existing_player_source=existing.source, existing_player_team_id=existing.current_team_id
            )

    player = Player(
        sport_id=team.sport_id,
        display_name=display_name.strip(),
        current_team_id=team_id,
        source=NEW_PLAYER_SOURCE_PREFIX,
        # Globally unique by construction (uuid4) — this player has no real
        # AFL Tables page yet, so there is no "real" source_player_id to
        # use; a future backfill that discovers their actual AFL Tables page
        # is a separate, explicit re-identification step, not automatic.
        source_player_id=f"manual-{uuid4().hex}",
        source_metadata={"note": note} if note else None,
        is_active=True,
    )
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def add_player_alias(db: Session, *, player_id: int, alias_name: str, source: str | None = None, note: str | None = None) -> PlayerAlias:
    player = db.get(Player, player_id)
    if player is None:
        raise ValueError(f"Player {player_id} does not exist")
    alias = PlayerAlias(player_id=player_id, alias_name=alias_name.strip(), source=source, note=note)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


def list_player_aliases(db: Session, *, player_id: int | None = None) -> list[PlayerAlias]:
    stmt = select(PlayerAlias).order_by(PlayerAlias.alias_name)
    if player_id is not None:
        stmt = stmt.where(PlayerAlias.player_id == player_id)
    return list(db.scalars(stmt).all())


def delete_player_alias(db: Session, alias_id: int) -> bool:
    alias = db.get(PlayerAlias, alias_id)
    if alias is None:
        return False
    db.delete(alias)
    db.commit()
    return True
