"""Frozen V1.5 outlook snapshots at fixed times before tip-off.

A snapshot label (e.g. "T-4h") targets the instant `tip-off - offset`. The
runner is invoked often (the live cycle wakes every 15 minutes). On each
call, for each upcoming game and each label:

- not yet at the target time                 -> nothing happens;
- within [target, target + tolerance(label)] -> produced NOW, with
                                                information cutoff = now;
- later than that                            -> recorded once as MISSED_WINDOW,
                                                no player rows (a "T-24h" made 3
                                                hours before tip-off is not a T-24h);
- already recorded for this label            -> skipped (frozen; never redone).

Tolerance per label: min(30 minutes, half the offset) - 30 minutes for
T-24h / T-4h / T-1h (two chances for a 15-minute runner), 15 minutes for T-30m
(so a "T-30m" is never taken inside the last quarter-hour).

Evidence freshness (EVIDENCE_LIMITS). Every source the snapshot depends on is
classified fresh / stale / unavailable at the cutoff and the actual ages are
frozen with the snapshot:

- roster (REQUIRED): the team's latest roster observation at or before the
  cutoff, aged from the last poll at or before the cutoff that still showed it
  (observations are written only on change; an unchanged roster is
  re-confirmed by each poll). Rosters are polled daily: fresh <= 36 h, stale <= 7 days; older or
  absent is unavailable - that team gets NO rows (no fallback to a
  reconstructed roster).
- recent box scores (REQUIRED for complete features): every final game of
  either team in the 7 days before the box-score cutoff must have its box
  score stored; otherwise stale (features would miss that game).
- schedule: the game's latest schedule observation (polled every 3 h): fresh
  <= 6 h, stale <= 48 h, else unavailable. Recorded; a stale schedule makes the
  snapshot stale.
- injury feed: latest read (polled every 30 min): fresh <= 90 min, stale <=
  24 h. Recorded only - V1.5 never uses it.

Status (most severe wins): missing_data (no rows) > partial (a team lacked a
usable roster) > stale (rows written, some required evidence stale) >
produced (everything fresh). A stale snapshot is frozen honestly but is never
labelled a normal success.

The learned fields (P(play), P(10+), E(minutes | plays), uncertainty) are
V1.5 and evidence-blind; availability evidence is stored beside them and never
changes them.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import NbaOutlookSnapshot
from app.models.nba.evidence import POLL_AVAILABILITY
from app.models.nba.outlook import SNAPSHOT_MISSED_WINDOW, SNAPSHOT_MISSING_DATA, SNAPSHOT_PARTIAL, SNAPSHOT_PRODUCED, SNAPSHOT_STALE
from app.nba.asof import GAME_RESULT_AVAILABILITY_LAG, _last_poll_at, game_schedule_known_at
from app.nba.minutes.data import load_games
from app.nba.rotation.prospective import ServingModels, compute_outlooks, history_for, load_serving, outlook_record, roster_evidence_for, upcoming_games, write_outlook

FRESH, STALE, UNAVAILABLE = "fresh", "stale", "unavailable"
STORED_BOX_STATES = ("ingested", "ingested_partial", "no_player_rows")


@dataclass(frozen=True)
class FreshnessLimit:
    fresh: timedelta
    stale: timedelta

    def classify(self, age: timedelta | None) -> str:
        if age is None or age > self.stale:
            return UNAVAILABLE
        return FRESH if age <= self.fresh else STALE


EVIDENCE_LIMITS = {
    "roster": FreshnessLimit(timedelta(hours=36), timedelta(days=7)),
    "schedule": FreshnessLimit(timedelta(hours=6), timedelta(hours=48)),
    "injury_feed": FreshnessLimit(timedelta(minutes=90), timedelta(hours=24)),
}
BOX_SCORE_LOOKBACK = timedelta(days=7)


@dataclass(frozen=True)
class SnapshotSpec:
    label: str
    offset: timedelta

    @property
    def tolerance(self) -> timedelta:
        return min(timedelta(minutes=30), self.offset / 2)


DEFAULT_SNAPSHOTS: tuple[SnapshotSpec, ...] = (
    SnapshotSpec("T-24h", timedelta(hours=24)),
    SnapshotSpec("T-4h", timedelta(hours=4)),
    SnapshotSpec("T-1h", timedelta(hours=1)),
    SnapshotSpec("T-30m", timedelta(minutes=30)),
)


def parse_specs(text: str) -> tuple[SnapshotSpec, ...]:
    """'T-24h,T-4h,T-90m' -> specs."""
    specs = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        if not part.startswith("T-") or part[-1] not in "hm":
            raise ValueError(f"bad snapshot label {part!r}; expected e.g. T-4h or T-30m")
        n = float(part[2:-1])
        specs.append(SnapshotSpec(part, timedelta(hours=n) if part[-1] == "h" else timedelta(minutes=n)))
    return tuple(specs)


@dataclass
class SnapshotRunResult:
    now: datetime
    produced: list[dict] = field(default_factory=list)
    recorded_without_rows: list[dict] = field(default_factory=list)
    skipped_existing: int = 0
    stats: dict = field(default_factory=dict)  # rows read, games / players processed

    def summary(self) -> str:
        lines = [f"now {self.now.isoformat()}  with rows {len(self.produced)}  recorded without rows {len(self.recorded_without_rows)}  already frozen {self.skipped_existing}"]
        if self.stats:
            lines.append(f"  read/processed: {self.stats}")
        for s in self.produced + self.recorded_without_rows:
            lines.append(f"  game {s['game_id']} {s['label']}: {s['status']} rows={s.get('rows', 0)} {s.get('reason') or ''}")
        return "\n".join(lines)


def _existing(db: Session, game_id: int, label: str, tipoff: datetime) -> bool:
    """Already recorded for this label and this scheduled tip-off? (A game
    moved to a new tip-off gets fresh snapshots for the new time.)"""
    tips = db.scalars(select(NbaOutlookSnapshot.scheduled_tipoff).where(NbaOutlookSnapshot.game_id == game_id, NbaOutlookSnapshot.snapshot_label == label)).all()
    return any(ensure_utc(t) == ensure_utc(tipoff) for t in tips)


def _age(now: datetime, then: datetime | None) -> timedelta | None:
    return None if then is None else ensure_utc(now) - ensure_utc(then)


def _minutes(td: timedelta | None) -> float | None:
    return None if td is None else round(td.total_seconds() / 60.0, 1)


def evidence_freshness(db: Session, games: pd.DataFrame, game, now: datetime) -> dict:
    """Ages and fresh/stale/unavailable classes of every evidence source for
    one game at `now`. Reads only observations at or before `now`."""
    out: dict = {"limits": {k: {"fresh_minutes": v.fresh.total_seconds() / 60, "stale_minutes": v.stale.total_seconds() / 60} for k, v in EVIDENCE_LIMITS.items()}}
    rosters = {}
    for team in (int(game.home_team_id), int(game.away_team_id)):
        ev = roster_evidence_for(db, team, now)
        age = None if ev is None else timedelta(minutes=ev["age_minutes"])
        rosters[str(team)] = {"age_minutes": _minutes(age), "class": EVIDENCE_LIMITS["roster"].classify(age), "observation_id": None if ev is None else ev["observation_id"]}
    out["roster"] = rosters

    box_cutoff = pd.Timestamp(ensure_utc(now) - GAME_RESULT_AVAILABILITY_LAG).tz_localize(None)
    teams = {int(game.home_team_id), int(game.away_team_id)}
    recent = games[
        (games["status"] == "final")
        & (games["scheduled_start"] <= box_cutoff)
        & (games["scheduled_start"] >= box_cutoff - pd.Timedelta(BOX_SCORE_LOOKBACK))
        & (games["home_team_id"].isin(teams) | games["away_team_id"].isin(teams))
    ]
    missing = recent[~recent["box_score_state"].isin(STORED_BOX_STATES)]["game_id"].astype(int).tolist()
    out["box_scores"] = {"recent_final_games": int(len(recent)), "missing_box_scores": missing, "class": STALE if missing else FRESH}

    sched = game_schedule_known_at(db, int(game.game_id), now)
    s_age = None if sched is None else _age(now, sched.observed_at)
    out["schedule"] = {"age_minutes": _minutes(s_age), "class": EVIDENCE_LIMITS["schedule"].classify(s_age)}

    f_age = _age(now, _last_poll_at(db, POLL_AVAILABILITY, now))
    out["injury_feed"] = {"age_minutes": _minutes(f_age), "class": EVIDENCE_LIMITS["injury_feed"].classify(f_age), "used_by_model": False}
    return out


def _record(db, *, game_id, spec, tipoff, now, status, reason, roster_ev, serving, counts, freshness) -> NbaOutlookSnapshot:
    snap = NbaOutlookSnapshot(
        game_id=game_id, snapshot_label=spec.label, offset_minutes=int(spec.offset.total_seconds() // 60), scheduled_tipoff=tipoff,
        target_cutoff=tipoff - spec.offset, information_cutoff=now, box_score_cutoff=now - GAME_RESULT_AVAILABILITY_LAG, generated_at=now,
        status=status, reason=reason, roster_evidence={str(k): v for k, v in (roster_ev or {}).items()},
        availability_feed_last_read_at=_last_poll_at(db, POLL_AVAILABILITY, now), model_versions=serving.versions() if serving else {},
        counts=counts, evidence_freshness=freshness or {},
    )
    db.add(snap)
    db.flush()
    return snap


def run_due_snapshots(
    db: Session,
    *,
    now: datetime | None = None,
    specs: tuple[SnapshotSpec, ...] = DEFAULT_SNAPSHOTS,
    dry_run: bool = False,
) -> SnapshotRunResult:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    result = SnapshotRunResult(now=now)
    games = load_games(db)
    result.stats["schedule_rows_read"] = int(len(games))
    horizon_h = max(s.offset for s in specs).total_seconds() / 3600.0 + 0.01
    upcoming = upcoming_games(games, now, horizon_h)
    if upcoming.empty:
        return result

    due = []
    for game in upcoming.itertuples(index=False):
        tipoff = pd.Timestamp(game.scheduled_start).tz_localize("UTC").to_pydatetime()
        for spec in specs:
            if now < tipoff - spec.offset:
                continue
            if _existing(db, int(game.game_id), spec.label, tipoff):
                result.skipped_existing += 1
                continue
            due.append((game, spec, tipoff))
    if not due:
        return result

    try:
        serving: ServingModels | None = load_serving(db)
        error = None
    except (ValueError, FileNotFoundError) as exc:  # no usable serving models: record honestly, produce nothing
        serving, error = None, str(exc)

    logs = None
    for game, spec, tipoff in due:
        gid = int(game.game_id)
        info = {"game_id": gid, "label": spec.label}
        late = now - (tipoff - spec.offset)
        if late > spec.tolerance:
            reason = f"runner reached this game {late.total_seconds() / 60:.0f} min after the target cutoff (tolerance {spec.tolerance.total_seconds() / 60:.0f} min)"
            info.update(status=SNAPSHOT_MISSED_WINDOW, reason=reason)
            if not dry_run:
                _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=SNAPSHOT_MISSED_WINDOW, reason=reason, roster_ev=None, serving=serving, counts={}, freshness=None)
            result.recorded_without_rows.append(info)
            continue
        fresh = evidence_freshness(db, games, game, now)
        if serving is None:
            info.update(status=SNAPSHOT_MISSING_DATA, reason=error)
            if not dry_run:
                _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=SNAPSHOT_MISSING_DATA, reason=error, roster_ev=None, serving=None, counts={}, freshness=fresh)
            result.recorded_without_rows.append(info)
            continue
        usable = {int(t) for t, r in fresh["roster"].items() if r["class"] != UNAVAILABLE}
        if logs is None:
            logs = history_for(db, games, upcoming)
            result.stats["history_rows_read"] = int(len(logs))
        one = upcoming[upcoming["game_id"] == gid]
        feats, skipped, roster_ev = compute_outlooks(db, serving, games, logs, one, now, require_roster=True, allowed_teams=usable)

        problems = [f"team {t} roster {r['class']} (age {r['age_minutes']} min)" for t, r in fresh["roster"].items() if r["class"] == UNAVAILABLE]
        stale_notes = [f"team {t} roster stale (age {r['age_minutes']} min)" for t, r in fresh["roster"].items() if r["class"] == STALE]
        if fresh["box_scores"]["class"] == STALE:
            stale_notes.append(f"box scores missing for recent games {fresh['box_scores']['missing_box_scores']}")
        if fresh["schedule"]["class"] != FRESH:
            stale_notes.append(f"schedule observation {fresh['schedule']['class']} (age {fresh['schedule']['age_minutes']} min)")
        if feats.empty:
            status = SNAPSHOT_MISSING_DATA
        elif problems:
            status = SNAPSHOT_PARTIAL
        elif stale_notes:
            status = SNAPSHOT_STALE
        else:
            status = SNAPSHOT_PRODUCED
        reason = "; ".join(problems + stale_notes) or None
        counts = {k: v for k, v in skipped.items() if k != "teams_without_roster_evidence"} | {"rows": int(len(feats))}
        info.update(status=status, reason=reason, rows=int(len(feats)))
        result.stats["games_processed"] = result.stats.get("games_processed", 0) + 1
        result.stats["players_predicted"] = result.stats.get("players_predicted", 0) + int(len(feats))
        if dry_run:
            info["rows_preview"] = [outlook_record(db, r, now) for _, r in feats.iterrows()]
            info["evidence_freshness"] = fresh
        else:
            snap = _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=status, reason=reason, roster_ev=roster_ev, serving=serving, counts=counts, freshness=fresh)
            for _, r in feats.iterrows():
                write_outlook(db, serving, outlook_record(db, r, now), now, snapshot_id=snap.id)
        (result.produced if not feats.empty else result.recorded_without_rows).append(info)
    if not dry_run:
        db.commit()
    return result
