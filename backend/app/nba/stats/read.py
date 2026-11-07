"""Read-only view of one game's frozen Stat Model V1 predictions, for the
dashboard's research panel.

Everything shown comes from the frozen nba_prop_projections rows: nothing is
recomputed, refitted or priced against a line. The only derived numbers are
the 10th and 90th percentiles of each row's own stored distribution, and they
are given only where that stat's distribution passed its offline rule. Points
failed it, so points show a mean and no range.

No odds, no lines, no edges and no recommendations.
"""

from datetime import datetime

from scipy import stats as sps
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import NbaGame, NbaPlayer, NbaPlayerGameLog, NbaPropProjection, NbaTeam
from app.nba.stats import models as sm
from app.nba.stats.features import STATS
from app.nba.stats.prospective import FREEZE_LABELS, MARKETS, load_parameters

STAT_OF_MARKET = {m.value: stat for stat, m in MARKETS.items()}


def _iso(value: datetime | None) -> str | None:
    return None if value is None else ensure_utc(value).isoformat()


def nb_interval(mean: float, alpha: float, low: float = 0.1, high: float = 0.9) -> tuple[int, int]:
    """The low and high quantiles of the stored negative binomial."""
    mu = max(float(mean), sm.MIN_MEAN)
    n = 1.0 / float(alpha)
    p = n / (n + mu)
    return int(sps.nbinom.ppf(low, n, p)), int(sps.nbinom.ppf(high, n, p))


def get_stat_predictions(db: Session, game_id: int) -> dict | None:
    game = db.get(NbaGame, game_id)
    if game is None:
        return None
    parameters, _ = load_parameters()
    version = parameters["model_version"]
    rows = db.scalars(
        select(NbaPropProjection).where(NbaPropProjection.game_id == game_id, NbaPropProjection.model_version == version).order_by(NbaPropProjection.id)
    ).all()

    final = game.status == "final"
    logs = {log.player_id: log for log in db.scalars(select(NbaPlayerGameLog).where(NbaPlayerGameLog.game_id == game_id)).all()} if final else {}
    player_ids = {r.player_id for r in rows}
    players = {p.id: p for p in db.scalars(select(NbaPlayer).where(NbaPlayer.id.in_(player_ids))).all()} if player_ids else {}
    teams = {t.id: t for t in db.scalars(select(NbaTeam).where(NbaTeam.id.in_([game.home_team_id, game.away_team_id]))).all()}

    validation: dict[str, dict] = {}
    windows: dict[str, dict] = {}
    by_player: dict[int, dict] = {}
    for r in rows:
        stat = STAT_OF_MARKET.get(r.market)
        label = r.inputs.get("snapshot_label")
        if stat is None or label not in FREEZE_LABELS:
            continue
        status = r.inputs.get("validation", {})
        validation.setdefault(stat, {**status, "distribution_kind": r.distribution_kind})
        windows.setdefault(label, {"label": label, "information_cutoff": _iso(r.information_cutoff), "generated_at": _iso(r.generated_at), "snapshot_id": r.inputs.get("snapshot_id")})
        calibrated = status.get("calibrated") is True
        interval = nb_interval(r.predicted_mean, r.distribution_params["alpha"]) if calibrated else None
        player = by_player.setdefault(
            r.player_id,
            {
                "player_id": r.player_id, "team_id": r.team_id,
                "name": players[r.player_id].display_name if r.player_id in players else f"player {r.player_id}",
                "position": players[r.player_id].position if r.player_id in players else None,
                "predictions": {},
            },
        )
        player["predictions"].setdefault(label, {"expected_minutes": r.expected_minutes, "p_play": r.inputs.get("p_play"), "stats": {}})["stats"][stat] = {
            "mean": r.predicted_mean, "rate_per_minute": r.rate_per_minute, "interval_80": None if interval is None else {"p10": interval[0], "p90": interval[1]},
            "calibrated": calibrated, "baselines": r.inputs.get("baselines", {}),
        }

    for pid, player in by_player.items():
        log = logs.get(pid)
        played = log is not None and log.did_not_play is False and log.minutes is not None
        player["result"] = None if not final else {"played": played, "minutes": log.minutes if played else None, **{s: (getattr(log, s) if played else None) for s in STATS}}
        latest = next((player["predictions"][lbl] for lbl in reversed(FREEZE_LABELS) if lbl in player["predictions"]), None)
        player["_sort"] = -(latest["expected_minutes"] or 0.0) if latest else 0.0

    def team_block(team_id: int) -> dict:
        team = teams.get(team_id)
        members = sorted((p for p in by_player.values() if p["team_id"] == team_id), key=lambda p: (p["_sort"], p["name"]))
        return {"id": team_id, "name": team.name if team else None, "abbreviation": team.abbreviation if team else None, "players": members}

    home, away = team_block(game.home_team_id), team_block(game.away_team_id)
    for p in by_player.values():
        p.pop("_sort", None)
    return {
        "game_id": game.id, "status": game.status, "tipoff": _iso(game.scheduled_start), "model_name": parameters["model_name"], "model_version": version,
        "labels": list(FREEZE_LABELS), "windows": [windows[lbl] for lbl in FREEZE_LABELS if lbl in windows], "validation": validation,
        "baselines": list(sm.BASELINES), "rows": len(rows), "home": home, "away": away,
    }

