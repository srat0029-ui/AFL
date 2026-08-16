"""One-off verification script: populates ExpectedLineup rows for the next
upcoming round using each team's most recent completed-match roster as a
placeholder "expected_in" status, through the exact same upsert path the
real PUT /api/afl/matches/{match_id}/lineup/{player_id} endpoint uses.

This is a stand-in for the real manual-entry UI, used only to generate real
projections end-to-end for verification. Every row is clearly notable as
placeholder data (note field), and this script is not part of the product -
run `python -m app.player_modelling.cli project-upcoming` against real,
manually-confirmed lineups for actual use.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ExpectedLineup, Match, MatchStatus, PlayerMatchStat
from app.player_modelling.upcoming_features import load_next_upcoming_round


def main() -> None:
    db = SessionLocal()
    matches = load_next_upcoming_round(db)
    print(f"Seeding placeholder lineups for {len(matches)} upcoming matches...")

    n_created = 0
    for m in matches:
        for team_id in (m.home_team_id, m.away_team_id):
            last_match_id = db.execute(
                select(PlayerMatchStat.match_id)
                .join(Match, Match.id == PlayerMatchStat.match_id)
                .where(PlayerMatchStat.team_id == team_id, Match.status == MatchStatus.COMPLETED)
                .order_by(Match.scheduled_start.desc())
                .limit(1)
            ).scalar_one_or_none()
            if last_match_id is None:
                continue
            player_ids = db.scalars(
                select(PlayerMatchStat.player_id).where(
                    PlayerMatchStat.team_id == team_id, PlayerMatchStat.match_id == last_match_id
                )
            ).all()
            for player_id in player_ids:
                existing = db.scalar(
                    select(ExpectedLineup).where(ExpectedLineup.match_id == m.match_id, ExpectedLineup.player_id == player_id)
                )
                if existing is not None:
                    continue
                db.add(
                    ExpectedLineup(
                        match_id=m.match_id, player_id=player_id, team_id=team_id, status="expected_in",
                        recorded_at=datetime.now(timezone.utc), source="manual",
                        note="PLACEHOLDER: seeded from most recent match roster for engine verification, not a confirmed lineup.",
                    )
                )
                n_created += 1
    db.commit()
    print(f"Created {n_created} placeholder ExpectedLineup rows.")
    db.close()


if __name__ == "__main__":
    main()
