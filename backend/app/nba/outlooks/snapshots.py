"""Frozen V1.5 outlook snapshots at fixed times before tip-off.

A snapshot label (e.g. "T-4h") targets the instant `tip-off - offset`. The
runner is meant to be invoked often (e.g. every 15 minutes). On each call,
for each upcoming game and each label:

- not yet at the target time            -> nothing happens;
- within [target, target + tolerance]   -> the snapshot is produced NOW, with
                                           information cutoff = now;
- later than target + tolerance          -> recorded once as MISSED_WINDOW, with
                                           no player rows (a "T-24h" made 3 hours
                                           before tip-off would not be a T-24h);
- already recorded for this label       -> skipped (frozen; never redone).

Different snapshots legitimately see different evidence: each reads only what
was observed at or before its own cutoff (roster observations, injury feed,
box scores final >= 4 h earlier). Nothing later is ever used.

The candidate universe is the team's roster OBSERVED at or before the
cutoff - valid pregame information. A team with no roster observation by
then produces no rows, and the snapshot records that (PARTIAL or
MISSING_DATA) instead of guessing a roster.

The learned fields (P(play), P(10+), E(minutes | plays), uncertainty) are
V1.5 and evidence-blind; availability evidence is stored beside them and
never changes them.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prospective import ensure_utc
from app.models.nba import NbaOutlookSnapshot
from app.models.nba.evidence import POLL_AVAILABILITY
from app.models.nba.outlook import SNAPSHOT_MISSED_WINDOW, SNAPSHOT_MISSING_DATA, SNAPSHOT_PARTIAL, SNAPSHOT_PRODUCED
from app.nba.asof import GAME_RESULT_AVAILABILITY_LAG, _last_poll_at
from app.nba.minutes.data import load_games
from app.nba.rotation.prospective import ServingModels, compute_outlooks, history_for, load_serving, outlook_record, upcoming_games, write_outlook


@dataclass(frozen=True)
class SnapshotSpec:
    label: str
    offset: timedelta


DEFAULT_SNAPSHOTS: tuple[SnapshotSpec, ...] = (
    SnapshotSpec("T-24h", timedelta(hours=24)),
    SnapshotSpec("T-4h", timedelta(hours=4)),
    SnapshotSpec("T-1h", timedelta(hours=1)),
    SnapshotSpec("T-30m", timedelta(minutes=30)),
)
# A 15-minute runner reaches every target within 15 minutes; 20 allows for a slow run.
DEFAULT_TOLERANCE = timedelta(minutes=20)


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

    def summary(self) -> str:
        lines = [f"now {self.now.isoformat()}  produced {len(self.produced)}  recorded without rows {len(self.recorded_without_rows)}  already frozen {self.skipped_existing}"]
        for s in self.produced + self.recorded_without_rows:
            lines.append(f"  game {s['game_id']} {s['label']}: {s['status']} rows={s.get('rows', 0)} {s.get('reason') or ''}")
        return "\n".join(lines)


def _existing(db: Session, game_id: int, label: str, tipoff: datetime) -> bool:
    """Already recorded for this label and this scheduled tip-off? (A game
    moved to a new tip-off gets fresh snapshots for the new time.)"""
    tips = db.scalars(select(NbaOutlookSnapshot.scheduled_tipoff).where(NbaOutlookSnapshot.game_id == game_id, NbaOutlookSnapshot.snapshot_label == label)).all()
    return any(ensure_utc(t) == ensure_utc(tipoff) for t in tips)


def _record(db, *, game_id, spec, tipoff, now, status, reason, roster_ev, serving, counts) -> NbaOutlookSnapshot:
    snap = NbaOutlookSnapshot(
        game_id=game_id, snapshot_label=spec.label, offset_minutes=int(spec.offset.total_seconds() // 60), scheduled_tipoff=tipoff,
        target_cutoff=tipoff - spec.offset, information_cutoff=now, box_score_cutoff=now - GAME_RESULT_AVAILABILITY_LAG, generated_at=now,
        status=status, reason=reason, roster_evidence={str(k): v for k, v in (roster_ev or {}).items()},
        availability_feed_last_read_at=_last_poll_at(db, POLL_AVAILABILITY, now), model_versions=serving.versions() if serving else {}, counts=counts,
    )
    db.add(snap)
    db.flush()
    return snap


def run_due_snapshots(
    db: Session,
    *,
    now: datetime | None = None,
    specs: tuple[SnapshotSpec, ...] = DEFAULT_SNAPSHOTS,
    tolerance: timedelta = DEFAULT_TOLERANCE,
    dry_run: bool = False,
) -> SnapshotRunResult:
    now = ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    result = SnapshotRunResult(now=now)
    games = load_games(db)
    horizon_h = max(s.offset for s in specs).total_seconds() / 3600.0 + 0.01
    upcoming = upcoming_games(games, now, horizon_h)
    if upcoming.empty:
        return result

    due: list[tuple[pd.Series, SnapshotSpec, datetime]] = []
    for game in upcoming.itertuples(index=False):
        tipoff = pd.Timestamp(game.scheduled_start).tz_localize("UTC").to_pydatetime()
        for spec in specs:
            target = tipoff - spec.offset
            if now < target:
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
    except ValueError as exc:  # no serving models: record honestly, produce nothing
        serving, error = None, str(exc)

    logs = None
    for game, spec, tipoff in due:
        gid = int(game.game_id)
        info = {"game_id": gid, "label": spec.label}
        if now > tipoff - spec.offset + tolerance:
            info.update(status=SNAPSHOT_MISSED_WINDOW, reason=f"runner reached this game {(now - (tipoff - spec.offset)).total_seconds() / 60:.0f} min after the target cutoff")
            if not dry_run:
                _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=SNAPSHOT_MISSED_WINDOW, reason=info["reason"], roster_ev=None, serving=serving, counts={})
            result.recorded_without_rows.append(info)
            continue
        if serving is None:
            info.update(status=SNAPSHOT_MISSING_DATA, reason=error)
            if not dry_run:
                _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=SNAPSHOT_MISSING_DATA, reason=error, roster_ev=None, serving=None, counts={})
            result.recorded_without_rows.append(info)
            continue
        if logs is None:
            logs = history_for(db, games, upcoming)
        one = upcoming[upcoming["game_id"] == gid]
        feats, skipped, roster_ev = compute_outlooks(db, serving, games, logs, one, now, require_roster=True)
        missing = skipped.get("teams_without_roster_evidence", [])
        if feats.empty:
            status = SNAPSHOT_MISSING_DATA
            reason = "no roster observation at or before the cutoff for team(s) " + ", ".join(map(str, missing)) if missing else "no predictable candidates"
        else:
            status = SNAPSHOT_PARTIAL if missing else SNAPSHOT_PRODUCED
            reason = ("no roster observation at or before the cutoff for team(s) " + ", ".join(map(str, missing))) if missing else None
        counts = {k: v for k, v in skipped.items() if k != "teams_without_roster_evidence"} | {"rows": int(len(feats))}
        info.update(status=status, reason=reason, rows=int(len(feats)))
        if dry_run:
            info["rows_preview"] = [outlook_record(db, r, now) for _, r in feats.iterrows()]
        else:
            snap = _record(db, game_id=gid, spec=spec, tipoff=tipoff, now=now, status=status, reason=reason, roster_ev=roster_ev, serving=serving, counts=counts)
            for _, r in feats.iterrows():
                write_outlook(db, serving, outlook_record(db, r, now), now, snapshot_id=snap.id)
        (result.produced if not feats.empty else result.recorded_without_rows).append(info)
    if not dry_run:
        db.commit()
    return result
