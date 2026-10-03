"""Frozen Model V1 expected-minutes predictions for upcoming 2026-27 games.

At prediction time `now`:
- information cutoff = now; features go through the SAME builder as the
  historical backtest (build_features with cutoff=now), so a box score
  counts only if its game was final and tipped off at least
  GAME_RESULT_AVAILABILITY_LAG before now;
- only games that have not tipped off are predicted, and each row is
  written once with its cutoff (append-only, guarded at the ORM layer);
- nothing is settled or evaluated here - the outcome does not exist yet.

Who is predicted for each team (the "candidate" players):
1. the team's listed roster as last OBSERVED at or before the cutoff
   (nba_team_observations, kind="roster") - the prospective equivalent of
   the historical box-score listing, which is how a historical row knows a
   player's team at tip-off; or, if the team has never been observed,
2. players whose latest known box score was for that team, in the target
   season or the one before it.
NbaPlayer.current_team_id is never used: it is today's value, not the
value at the cutoff.

Model V1 deliberately uses NO injury/availability evidence: a player listed
as out still gets a "minutes if he plays" prediction. Combining that
evidence is Model V2's job.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import InformationLeakError, ensure_utc
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaGameStatus, NbaMinutesModelRun, NbaMinutesPrediction, NbaPlayer
from app.models.nba.evidence import TEAM_OBSERVATION_ROSTER
from app.nba.asof import team_observation_known_at
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, history_logs
from app.nba.minutes.features import build_features
from app.nba.minutes.registry import load_model
from app.nba.minutes.residuals import apply_uncertainty

TEAM_SOURCE_ROSTER = "roster_observation"
TEAM_SOURCE_BOX_SCORE = "last_box_score"


@dataclass
class PredictUpcomingResult:
    cutoff: datetime
    model_run_id: int | None
    games: int = 0
    candidates: int = 0
    predicted: int = 0
    written: int = 0
    skipped: dict = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"cutoff {self.cutoff.isoformat()}  model run {self.model_run_id}",
            f"games {self.games}  candidates {self.candidates}  predicted {self.predicted}  written {self.written}",
            f"skipped {self.skipped}",
        ]
        for r in self.rows[:40]:
            lines.append(f"  game {r['game_id']} team {r['team_id']} player {r['player_id']}: {r['expected_minutes']:.1f} min  (p10 {r['p10']:.0f} - p90 {r['p90']:.0f})  [{r['team_source']}]")
        if len(self.rows) > 40:
            lines.append(f"  ... {len(self.rows) - 40} more")
        return "\n".join(lines)


def latest_serving_run(db: Session) -> NbaMinutesModelRun | None:
    return db.scalar(select(NbaMinutesModelRun).where(NbaMinutesModelRun.purpose == "serving").order_by(NbaMinutesModelRun.id.desc()).limit(1))


def candidate_rows(db: Session, upcoming: pd.DataFrame, played_history: pd.DataFrame, games: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    """(player_id, game_id, team_id, team_source) for every player to predict."""
    by_source_id = {pid: id_ for id_, pid in db.execute(select(NbaPlayer.id, NbaPlayer.source_player_id).where(NbaPlayer.source == "espn")).all()}
    known = played_history.merge(games[["game_id", "scheduled_start", "season_start_year"]], on="game_id")
    known = known[known["scheduled_start"] <= pd.Timestamp(ensure_utc(cutoff)).tz_localize(None) - pd.Timedelta(hours=4)]
    last_team = known.sort_values("scheduled_start").groupby("player_id").tail(1)
    rows = []
    for game in upcoming.itertuples():
        for team_id in (game.home_team_id, game.away_team_id):
            obs = team_observation_known_at(db, int(team_id), TEAM_OBSERVATION_ROSTER, cutoff)
            if obs is not None:
                for p in obs.payload.get("players", []):
                    pid = by_source_id.get(str(p.get("id")))
                    rows.append({"player_id": pid, "game_id": game.game_id, "team_id": int(team_id), "team_source": TEAM_SOURCE_ROSTER, "source_player_id": str(p.get("id"))})
            else:
                recent = last_team[(last_team["team_id"] == team_id) & (last_team["season_start_year"] >= game.season_start_year - 1)]
                for pid in recent["player_id"]:
                    rows.append({"player_id": int(pid), "game_id": game.game_id, "team_id": int(team_id), "team_source": TEAM_SOURCE_BOX_SCORE, "source_player_id": None})
    return pd.DataFrame(rows, columns=["player_id", "game_id", "team_id", "team_source", "source_player_id"])


def predict_upcoming(
    db: Session, *, hours_ahead: float = 36.0, model_run_id: int | None = None, dry_run: bool = False, now: datetime | None = None
) -> PredictUpcomingResult:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    run = db.get(NbaMinutesModelRun, model_run_id) if model_run_id is not None else latest_serving_run(db)
    if run is None or run.purpose != "serving":
        raise ValueError("no serving minutes-model run found - run `python -m app.nba.minutes.cli evaluate` first")
    result = PredictUpcomingResult(cutoff=now, model_run_id=run.id)
    model = load_model(run)

    games = load_games(db)
    now_naive = pd.Timestamp(now).tz_localize(None)
    upcoming = games[
        (games["status"] == NbaGameStatus.SCHEDULED.value)
        & games["season_type"].isin(COMPETITIVE_SEASON_TYPES)
        & (games["scheduled_start"] > now_naive)
        & (games["scheduled_start"] <= now_naive + pd.Timedelta(hours=hours_ahead))
    ]
    result.games = len(upcoming)
    if upcoming.empty:
        return result

    season = int(upcoming["season_start_year"].max())
    # Prospective history includes the current season's completed games - that is prior information here.
    logs = history_logs(load_logs(db, min_season=DEFAULT_FIRST_SEASON), games, DEFAULT_FIRST_SEASON, season)
    played = logs[(~logs["did_not_play"]) & logs["minutes"].notna()]
    cands = candidate_rows(db, upcoming, played, games, now)
    result.candidates = len(cands)
    unknown = cands["player_id"].isna()
    result.skipped["unknown_player"] = int(unknown.sum())
    cands = cands[~unknown].astype({"player_id": int}).drop_duplicates(["player_id", "game_id"]).reset_index(drop=True)
    if cands.empty:
        return result

    feats = build_features(games, logs, cands[["player_id", "game_id", "team_id"]], cutoff=now_naive)
    if (feats["history_last_start"].dropna() > now_naive - pd.Timedelta(hours=4)).any():
        raise InformationLeakError("a feature used a box score not yet known at the cutoff")
    feats["team_source"] = cands["team_source"].to_numpy()
    ok = feats[feats["eligible"]].copy()
    result.skipped["no_history"] = int((~feats["eligible"]).sum())
    if ok.empty:
        return result

    pred = np.clip(model.predict(ok[run.features].to_numpy(dtype=float)), 0.0, 60.0)
    quant = apply_uncertainty(pred, run.uncertainty)
    result.predicted = len(ok)
    for i, (idx, row) in enumerate(ok.iterrows()):
        q = quant.iloc[i]
        record = {
            "game_id": int(row["game_id"]),
            "player_id": int(row["player_id"]),
            "team_id": int(row["team_id"]),
            "expected_minutes": float(pred[i]),
            "team_source": row["team_source"],
            **{k: float(q[k]) for k in quant.columns},
        }
        result.rows.append(record)
        if dry_run:
            continue
        game = db.get(NbaGame, record["game_id"])
        tipoff = ensure_utc(game.scheduled_start)
        if game.status != NbaGameStatus.SCHEDULED.value or now >= tipoff:
            raise InformationLeakError(f"game {game.id} is not an upcoming game at {now.isoformat()}")
        db.add(
            NbaMinutesPrediction(
                model_run_id=run.id,
                model_version=run.model_version,
                game_id=record["game_id"],
                player_id=record["player_id"],
                team_id=record["team_id"],
                generated_at=now,
                information_cutoff=now,
                expected_minutes=record["expected_minutes"],
                quantiles={k: record[k] for k in quant.columns},
                history_games=int(row["career_games"]),
                team_source=record["team_source"],
                inputs={f: (None if pd.isna(row[f]) else float(row[f])) for f in run.features},
            )
        )
        result.written += 1
    if not dry_run:
        db.commit()
    return result


def frozen_predictions_before(db: Session, game_id: int) -> list[NbaMinutesPrediction]:
    """Every frozen prediction for a game, oldest first (for later evaluation
    once the game is final - not done in V1)."""
    return list(db.scalars(select(NbaMinutesPrediction).where(NbaMinutesPrediction.game_id == game_id).order_by(NbaMinutesPrediction.information_cutoff)).all())


__all__ = ["predict_upcoming", "candidate_rows", "latest_serving_run", "PredictUpcomingResult", "frozen_predictions_before"]
