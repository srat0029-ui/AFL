"""Frozen pregame rotation outlooks for upcoming games ("V1.5").

For each candidate player in each upcoming game, kept as SEPARATE fields:

- p_play                       P(he enters the game)                 learned, V1.5
- p_rotation                   P(he plays >= 10 minutes)              learned, V1.5;
                                                                      capped at p_play
- expected_minutes_if_plays    E(minutes | plays)                     V1, unchanged
- reconciled_minutes_if_plays  team-reconciled E(minutes | plays)     only if the
                                                                      reconciliation was adopted
- quantiles                    V1 uncertainty band on the conditional minutes
- tier                         rostered / likely rotation / likely active /
                               uncertain / unlikely - a label derived from the
                               probabilities, never a substitute for them
- availability_evidence        the RAW injury-feed state known at the cutoff
- experimental_rules           separately labelled prospective rules (e.g. a
                               reported "Out"); they NEVER change the learned
                               fields above
- the information cutoff and every model run id

Nothing is multiplied together: a prop is void if the player does not play,
so pricing needs E(minutes | plays) and P(play) as two numbers.

V1.5 is historically reconstructable information only. Minutes V2 will add
prospective availability evidence as its own layer on top of these frozen
fields; it does not redefine them.

Absence from ESPN's injury feed is NOT evidence of health: a player the feed
has never listed is recorded as "no evidence", and one it stopped listing
as "no longer listed", with no status implied.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import InformationLeakError, ensure_utc
from app.models.nba import COMPETITIVE_SEASON_TYPES, NbaGame, NbaGameStatus, NbaMinutesModelRun, NbaRotationPrediction
from app.nba.asof import availability_known_at
from app.nba.minutes.data import load_games, load_logs
from app.nba.minutes.dataset import DEFAULT_FIRST_SEASON, history_logs
from app.nba.minutes.features import build_features
from app.nba.minutes.prospective import TEAM_SOURCE_ROSTER, candidate_rows
from app.nba.minutes.registry import load_model
from app.nba.minutes.residuals import apply_uncertainty
from app.nba.rotation.allocation import band_sigma, reconcile_team
from app.nba.rotation.features import PARTICIPATION_FEATURES, add_participation_features

ROTATION_MODEL_VERSION = "rotation-v1.0"
MINUTES_MODEL_PREFIX = "minutes-v1"
NO_HISTORY_ALLOCATION_MINUTES = 9.0  # the documented V1 fallback; used ONLY inside the team total, never output

# Tier thresholds (applied to calibrated probabilities).
TIER_LIKELY_ROTATION = 0.7  # p_rotation
TIER_LIKELY_ACTIVE = 0.8  # p_play
TIER_UNLIKELY = 0.3  # p_play below this

RULE_OUT_STATUS = "experimental_out_status_v0"


def tier_for(p_play: float, p_rotation: float) -> str:
    if p_rotation >= TIER_LIKELY_ROTATION:
        return "likely_rotation"
    if p_play >= TIER_LIKELY_ACTIVE:
        return "likely_active"
    if p_play < TIER_UNLIKELY:
        return "unlikely"
    return "uncertain"


def availability_payload(db: Session, player_id: int, cutoff: datetime) -> dict:
    ev = availability_known_at(db, player_id, cutoff)
    base = {"last_feed_read_at": ev.last_confirmed_at.isoformat() if ev.last_confirmed_at else None}
    if ev.last_confirmed_at is None:
        return {"state": "feed_not_read_by_cutoff", **base}
    if ev.observation is None:
        return {"state": "never_listed", "note": "no evidence either way - NOT evidence of health", **base}
    obs = ev.observation
    if not obs.is_listed:
        return {"state": "no_longer_listed", "since": ev.first_observed_at.isoformat(), "note": "no status implied", **base}
    return {
        "state": "listed",
        "status": obs.status,
        "source_status": obs.source_status,
        "observed_since": ev.first_observed_at.isoformat(),
        "injury_type": obs.injury_type,
        "injury_detail": obs.injury_detail,
        "expected_return_date": obs.expected_return_date.isoformat() if obs.expected_return_date else None,
        **base,
    }


def experimental_rules(evidence: dict) -> dict:
    """Separately labelled prospective rules. Not learned, not validated
    historically (there is no history of this evidence yet), and never
    applied to the learned fields."""
    out = {}
    if evidence.get("state") == "listed" and evidence.get("status") == "out":
        out[RULE_OUT_STATUS] = {"p_play": 0.0, "basis": "injury feed listed the player as Out at the cutoff", "applied_to_learned_fields": False}
    return out


@dataclass
class RotationOutlookResult:
    cutoff: datetime
    run_ids: dict
    games: int = 0
    candidates: int = 0
    predicted: int = 0
    written: int = 0
    skipped: dict = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"cutoff {self.cutoff.isoformat()}  runs {self.run_ids}", f"games {self.games}  candidates {self.candidates}  predicted {self.predicted}  written {self.written}  skipped {self.skipped}"]
        for r in self.rows[:40]:
            m = "-" if r["expected_minutes_if_plays"] is None else f"{r['expected_minutes_if_plays']:.1f}"
            rec = "-" if r["reconciled_minutes_if_plays"] is None else f"{r['reconciled_minutes_if_plays']:.1f}"
            lines.append(f"  g{r['game_id']} t{r['team_id']} p{r['player_id']}: P(play) {r['p_play']:.2f}  P(10+) {r['p_rotation']:.2f}  E(min|play) {m}  recon {rec}  [{r['tier']}] avail={r['availability_evidence'].get('state')}")
        return "\n".join(lines)


def _latest_run(db: Session, *, model_version_prefix: str, model_name_prefix: str | None = None) -> NbaMinutesModelRun | None:
    stmt = select(NbaMinutesModelRun).where(NbaMinutesModelRun.purpose == "serving", NbaMinutesModelRun.model_version.startswith(model_version_prefix))
    if model_name_prefix:
        stmt = stmt.where(NbaMinutesModelRun.model_name.startswith(model_name_prefix))
    return db.scalar(stmt.order_by(NbaMinutesModelRun.id.desc()).limit(1))


def predict_rotation_upcoming(db: Session, *, hours_ahead: float = 36.0, dry_run: bool = False, now: datetime | None = None) -> RotationOutlookResult:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    minutes_run = _latest_run(db, model_version_prefix=MINUTES_MODEL_PREFIX)
    play_run = _latest_run(db, model_version_prefix=ROTATION_MODEL_VERSION, model_name_prefix="participation_")
    rot_run = _latest_run(db, model_version_prefix=ROTATION_MODEL_VERSION, model_name_prefix="rotation_10_")
    recon_run = _latest_run(db, model_version_prefix=ROTATION_MODEL_VERSION, model_name_prefix="reconciliation")
    if None in (minutes_run, play_run, rot_run, recon_run):
        raise ValueError("missing serving runs - run the minutes and rotation experiments first")
    result = RotationOutlookResult(cutoff=now, run_ids={"minutes": minutes_run.id, "participation": play_run.id, "rotation": rot_run.id, "reconciliation": recon_run.id})
    v1, play_model, rot_model = load_model(minutes_run), load_model(play_run), load_model(rot_run)
    recon_cfg = recon_run.hyperparameters

    games = load_games(db)
    now_naive = pd.Timestamp(now).tz_localize(None)
    upcoming = games[
        (games["status"] == NbaGameStatus.SCHEDULED.value) & games["season_type"].isin(COMPETITIVE_SEASON_TYPES)
        & (games["scheduled_start"] > now_naive) & (games["scheduled_start"] <= now_naive + pd.Timedelta(hours=hours_ahead))
    ]
    result.games = len(upcoming)
    if upcoming.empty:
        return result
    season = int(upcoming["season_start_year"].max())
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
    feats = add_participation_features(feats, games, logs)
    if (feats["history_last_start"].dropna() > now_naive - pd.Timedelta(hours=4)).any():
        raise InformationLeakError("a feature used a box score not yet known at the cutoff")
    feats["team_source"] = cands["team_source"].to_numpy()
    X = feats[PARTICIPATION_FEATURES].to_numpy(dtype=float)
    feats["p_play"] = play_model.predict_proba(X)[:, 1]
    # Two separately trained classifiers can disagree slightly; "plays 10+
    # minutes" implies "plays", so P(rotation) is capped at P(play). On the
    # 2025-26 walk-forward fold 14.5% of rows needed the cap (mean excess
    # 0.016) and it improved rotation log loss very slightly (0.27407 -> 0.27403).
    feats["p_rotation"] = np.minimum(rot_model.predict_proba(X)[:, 1], feats["p_play"].to_numpy())
    elig = feats["eligible"].astype(bool).to_numpy()
    feats["m_cond"] = np.nan
    if elig.any():
        feats.loc[elig, "m_cond"] = np.clip(v1.predict(feats.loc[elig, minutes_run.features].to_numpy(dtype=float)), 0.0, 60.0)
    result.skipped["no_history_minutes_not_estimated"] = int((~elig).sum())

    feats["m_alloc"] = feats["m_cond"].fillna(NO_HISTORY_ALLOCATION_MINUTES)
    feats["m_recon"] = np.nan
    if recon_cfg.get("adopted"):
        feats["sigma"] = band_sigma(feats["m_alloc"], recon_cfg["sigma_by_band"])
        ot = recon_cfg.get("overtime")
        budget = 240.0 + (25.0 * ot["periods_if_ot"] * ot["rate"] if ot else 0.0)
        for _, g in feats.groupby(["game_id", "team_id"]):
            adj = reconcile_team(g["m_alloc"], g["p_play"], g["sigma"], budget, recon_cfg["alpha"], recon_cfg["method"])
            feats.loc[g.index, "m_recon"] = np.where(g["eligible"].astype(bool), adj, np.nan)
    quant = apply_uncertainty(feats["m_cond"].fillna(0.0), minutes_run.uncertainty)

    result.predicted = len(feats)
    for i, row in feats.iterrows():
        evidence = availability_payload(db, int(row["player_id"]), now)
        record = {
            "game_id": int(row["game_id"]), "player_id": int(row["player_id"]), "team_id": int(row["team_id"]),
            "p_play": float(row["p_play"]), "p_rotation": float(row["p_rotation"]),
            "expected_minutes_if_plays": None if pd.isna(row["m_cond"]) else float(row["m_cond"]),
            "reconciled_minutes_if_plays": None if pd.isna(row["m_recon"]) else float(row["m_recon"]),
            "quantiles": None if pd.isna(row["m_cond"]) else {k: float(quant.loc[i, k]) for k in quant.columns},
            "tier": tier_for(float(row["p_play"]), float(row["p_rotation"])),
            "team_source": row["team_source"], "roster_listed": row["team_source"] == TEAM_SOURCE_ROSTER,
            "availability_evidence": evidence, "experimental_rules": experimental_rules(evidence),
        }
        result.rows.append(record)
        if dry_run:
            continue
        game = db.get(NbaGame, record["game_id"])
        if game.status != NbaGameStatus.SCHEDULED.value or now >= ensure_utc(game.scheduled_start):
            raise InformationLeakError(f"game {game.id} is not an upcoming game at {now.isoformat()}")
        db.add(NbaRotationPrediction(
            game_id=record["game_id"], player_id=record["player_id"], team_id=record["team_id"], generated_at=now, information_cutoff=now,
            minutes_model_run_id=minutes_run.id, participation_model_run_id=play_run.id, rotation_model_run_id=rot_run.id, reconciliation_model_run_id=recon_run.id,
            p_play=record["p_play"], p_rotation=record["p_rotation"], expected_minutes_if_plays=record["expected_minutes_if_plays"],
            reconciled_minutes_if_plays=record["reconciled_minutes_if_plays"], quantiles=record["quantiles"], tier=record["tier"],
            team_source=record["team_source"], roster_listed=record["roster_listed"], history_games=int(row["career_games"]),
            availability_evidence=evidence, experimental_rules=record["experimental_rules"],
            inputs={f: (None if pd.isna(row[f]) else float(row[f])) for f in PARTICIPATION_FEATURES},
        ))
        result.written += 1
    if not dry_run:
        db.commit()
    return result
