"""Read-only views of the frozen V1.5 outlooks for the NBA dashboard
(GET /api/nba/outlooks/...).

Everything comes from what is already stored: nba_outlook_snapshots (one
per game and window, frozen before tip-off) and the nba_rotation_predictions
linked to them, plus each player's CURRENT injury-feed state (as of the
request) beside what each snapshot saw. Nothing here sends a request to any
provider or writes to the database.

Window states, per game and label (T-24h, T-4h, T-1h, T-30m):
- a snapshot's own status (produced / stale / partial / missing_data /
  missed_window) when one was frozen for the game's CURRENT tip-off;
- "upcoming" when the window's target cutoff is still ahead;
- "none" when the window has passed with nothing frozen for this tip-off
  (e.g. before snapshots were switched on, or a rescheduled game).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaOutlookSnapshot, NbaPlayer, NbaPlayerGameLog, NbaRotationPrediction, NbaTeam
from app.nba.outlooks.snapshots import DEFAULT_SNAPSHOTS
from app.nba.rotation.prospective import availability_payload

PRODUCED = "produced"
UPCOMING = "upcoming"
NONE = "none"
LABELS = tuple(spec.label for spec in DEFAULT_SNAPSHOTS)
OFFSETS = {spec.label: spec.offset for spec in DEFAULT_SNAPSHOTS}


def _iso(value: datetime | None) -> str | None:
    return ensure_utc(value).isoformat() if value is not None else None


def _team(team: NbaTeam) -> dict:
    return {"id": team.id, "abbreviation": team.abbreviation, "name": team.name}


def _current_snapshots(db: Session, games: list[NbaGame]) -> dict[tuple[int, str], NbaOutlookSnapshot]:
    """Per (game, label): the snapshot frozen for the game's CURRENT tip-off,
    preferring a produced one, then the latest."""
    if not games:
        return {}
    start = {g.id: ensure_utc(g.scheduled_start) for g in games}
    rows = db.scalars(select(NbaOutlookSnapshot).where(NbaOutlookSnapshot.game_id.in_(list(start))).order_by(NbaOutlookSnapshot.id)).all()
    chosen: dict[tuple[int, str], NbaOutlookSnapshot] = {}
    for snap in rows:
        if ensure_utc(snap.scheduled_tipoff) != start[snap.game_id]:
            continue
        key = (snap.game_id, snap.snapshot_label)
        current = chosen.get(key)
        if current is None or (snap.status == PRODUCED) >= (current.status == PRODUCED):
            chosen[key] = snap
    return chosen


def _window(game: NbaGame, label: str, snap: NbaOutlookSnapshot | None, now: datetime) -> dict:
    target = ensure_utc(game.scheduled_start) - OFFSETS[label]
    if snap is not None:
        state = snap.status
    elif now < target:
        state = UPCOMING
    else:
        state = NONE
    out = {"label": label, "state": state, "target_cutoff": target.isoformat(), "snapshot_id": None if snap is None else snap.id}
    if snap is not None:
        out.update({
            "reason": snap.reason, "generated_at": _iso(snap.generated_at), "information_cutoff": _iso(snap.information_cutoff),
            "model_versions": snap.model_versions or {}, "counts": snap.counts or {}, "evidence_freshness": snap.evidence_freshness or {},
            "availability_feed_last_read_at": _iso(snap.availability_feed_last_read_at),
        })
    return out


def list_outlook_games(db: Session, *, now: datetime | None = None, hours_back: float = 6.0, hours_ahead: float = 48.0) -> dict:
    """Competitive games tipping off from `hours_back` ago to `hours_ahead`
    ahead, each with the state of its four snapshot windows."""
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    games = db.scalars(
        select(NbaGame)
        .where(NbaGame.season_type.in_(COMPETITIVE_SEASON_TYPES))
        .where(NbaGame.scheduled_start >= now - timedelta(hours=hours_back), NbaGame.scheduled_start <= now + timedelta(hours=hours_ahead))
        .order_by(NbaGame.scheduled_start, NbaGame.id)
    ).all()
    snaps = _current_snapshots(db, list(games))
    teams = {t.id: t for t in db.scalars(select(NbaTeam).where(NbaTeam.id.in_({g.home_team_id for g in games} | {g.away_team_id for g in games}))).all()} if games else {}
    out = []
    for g in games:
        windows = [_window(g, label, snaps.get((g.id, label)), now) for label in LABELS]
        produced = [w["label"] for w in windows if w["state"] == PRODUCED]
        out.append({
            "game_id": g.id, "tipoff": _iso(g.scheduled_start), "status": g.status, "season_type": g.season_type,
            "home": _team(teams[g.home_team_id]), "away": _team(teams[g.away_team_id]),
            "windows": [{k: w[k] for k in ("label", "state", "target_cutoff", "snapshot_id", "generated_at") if k in w} for w in windows],
            "latest_produced": produced[-1] if produced else None,
        })
    return {"as_of": now.isoformat(), "window": {"hours_back": hours_back, "hours_ahead": hours_ahead}, "games": out}


def get_outlook_game(db: Session, game_id: int, *, now: datetime | None = None) -> dict | None:
    """One game: its four windows (full snapshot metadata) and every player
    with an outlook in any of them, side by side, with the player's current
    injury-feed state and - once the game is final - what happened."""
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    game = db.get(NbaGame, game_id)
    if game is None:
        return None
    home, away = db.get(NbaTeam, game.home_team_id), db.get(NbaTeam, game.away_team_id)
    snaps = _current_snapshots(db, [game])
    windows = [_window(game, label, snaps.get((game.id, label)), now) for label in LABELS]
    label_of = {s.id: label for (_, label), s in snaps.items()}

    preds = db.scalars(select(NbaRotationPrediction).where(NbaRotationPrediction.snapshot_id.in_(list(label_of)))).all() if label_of else []
    player_ids = {p.player_id for p in preds}
    players = {p.id: p for p in db.scalars(select(NbaPlayer).where(NbaPlayer.id.in_(player_ids))).all()} if player_ids else {}
    final = game.status == "final"
    logs = {log.player_id: log for log in db.scalars(select(NbaPlayerGameLog).where(NbaPlayerGameLog.game_id == game.id)).all()} if final else {}

    rows: dict[tuple[int, int], dict] = {}
    for p in preds:
        row = rows.setdefault((p.team_id, p.player_id), {"player_id": p.player_id, "team_id": p.team_id, "outlooks": {}})
        row["outlooks"][label_of[p.snapshot_id]] = {
            "p_play": p.p_play, "p_rotation": p.p_rotation, "expected_minutes_if_plays": p.expected_minutes_if_plays,
            "quantiles": p.quantiles, "tier": p.tier, "history_games": p.history_games, "team_source": p.team_source,
            "roster_listed": p.roster_listed, "availability_evidence": p.availability_evidence or {},
            "generated_at": _iso(p.generated_at), "information_cutoff": _iso(p.information_cutoff),
        }
    for row in rows.values():
        player = players.get(row["player_id"])
        row["name"] = player.display_name if player else f"player {row['player_id']}"
        row["position"] = player.position if player else None
        row["current_availability"] = availability_payload(db, row["player_id"], now)
        log = logs.get(row["player_id"])
        row["result"] = None if not final else {
            "dressed": log is not None, "played": log is not None and not log.did_not_play, "minutes": None if log is None else log.minutes,
        }
        latest = next((row["outlooks"][label] for label in reversed(LABELS) if label in row["outlooks"]), None)
        row["latest_label"] = next((label for label in reversed(LABELS) if label in row["outlooks"]), None)
        row["_sort"] = (-(latest["p_rotation"] or 0.0), -(latest["expected_minutes_if_plays"] or 0.0), row["name"]) if latest else (0.0, 0.0, row["name"])

    def team_block(team: NbaTeam) -> dict:
        members = sorted((r for (team_id, _), r in rows.items() if team_id == team.id), key=lambda r: r["_sort"])
        for r in members:
            r.pop("_sort", None)
        return {**_team(team), "players": members}

    return {
        "as_of": now.isoformat(), "game_id": game.id, "tipoff": _iso(game.scheduled_start), "status": game.status, "season_type": game.season_type,
        "box_score_state": game.box_score_state, "home": team_block(home), "away": team_block(away), "windows": windows, "labels": list(LABELS),
    }
