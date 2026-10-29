"""NBA production watchdog: turns the read-only checks gathered by
.github/workflows/nba-watchdog.yml into a list of problems and a coverage
summary. Standard library only, so the workflow runs it from the checkout
with the runner's own Python - no image, no database driver.

The 9-28 October 2026 outage went unnoticed for 19 days because a database
that refuses connections, or a cycle that is paused, only ever shows up as a
red or skipped run. The watchdog asks the questions directly, every 30
minutes, from outside the live cycle:

- evidence-health (the live cycle's own read-only monitor, app/nba/monitoring.py)
  exited 0 and reported OK - this also catches a stopped or paused cycle,
  because the injury feed goes stale after 90 minutes without a cycle;
- every snapshot window that has come due since snapshots were switched on
  (`--since`) was frozen with status "produced" - a missing row, a missed
  window, or stale / partial / missing_data evidence is a problem;
- every competitive game that tipped off 6 h to 3 days ago is final with a
  stored box score;
- the latest successful Backup run is at most `--backup-max-age-days` old.

A check that cannot run at all (database unreachable, query error) is itself
a problem. Nothing here, or in the workflow, writes to the database.

Exit 1 when there is any problem, 0 otherwise.
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

LABELS = ("T-24h", "T-4h", "T-1h", "T-30m")
PRODUCED = "produced"


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    health_lines: list[str] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)  # one row per game: game, tipoff, matchup, {label: cell}
    latest_backup: str = "unknown"


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def check_health(output: str, exit_code: int, report: Report) -> None:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    summary = next((line for line in lines if line.startswith("evidence health:")), None)
    if summary is None:
        tail = lines[-1] if lines else "no output"
        report.problems.append(f"evidence-health could not run (exit {exit_code}): {tail[:200]}")
        report.health_lines = lines[-15:]
        return
    report.health_lines = lines[lines.index(summary):]
    problems = [line.strip().removeprefix("problem:").strip() for line in report.health_lines if line.strip().startswith("problem:")]
    if exit_code != 0 or not summary.startswith("evidence health: OK"):
        report.problems += [f"evidence: {p}" for p in problems] or [f"evidence-health exited {exit_code}: {summary}"]


def check_snapshots(rows: list[dict] | None, exit_code: int, report: Report) -> None:
    """rows: one per (game, label) from expected_snapshots.sql, with columns
    game_id, tipoff, matchup, label, due (t/f), status ('' when none)."""
    if rows is None or exit_code != 0:
        report.problems.append(f"snapshot coverage query failed (exit {exit_code})")
        return
    games: dict[str, dict] = {}
    for row in rows:
        game = games.setdefault(row["game_id"], {"game_id": row["game_id"], "tipoff": row["tipoff"], "matchup": row["matchup"], "cells": {}})
        due = row["due"].strip().lower() in ("t", "true")
        status = row["status"].strip()
        if due and status != PRODUCED:
            report.problems.append(f"snapshot {row['matchup']} {row['tipoff']} {row['label']}: {status or 'no snapshot'}")
        if status:
            cell = status
        elif due:
            cell = "MISSING"
        else:
            cell = "pending"
        game["cells"][row["label"]] = cell
    report.coverage = list(games.values())


def check_box_scores(rows: list[dict] | None, exit_code: int, report: Report) -> None:
    """rows from box_scores.sql: competitive games 6 h - 3 days past tip-off
    that are not final with a stored box score."""
    if rows is None or exit_code != 0:
        report.problems.append(f"box-score coverage query failed (exit {exit_code})")
        return
    for row in rows:
        report.problems.append(f"box score {row['matchup']} {row['tipoff']}: game {row['status']}, box score {row['box_score_state'] or 'never fetched'}")


def check_backups(runs: list[dict] | None, exit_code: int, now: datetime, max_age_days: float, report: Report) -> None:
    """runs: `gh run list --workflow backup.yml --status success --json createdAt` output."""
    if runs is None or exit_code != 0:
        report.problems.append(f"could not list Backup runs (exit {exit_code})")
        return
    if not runs:
        report.latest_backup = "none"
        report.problems.append("no successful database backup on record")
        return
    latest = max(_parse_time(run["createdAt"]) for run in runs)
    age_days = (now - latest).total_seconds() / 86400
    report.latest_backup = f"{latest:%Y-%m-%d %H:%MZ} ({age_days:.1f} days ago)"
    if age_days > max_age_days:
        report.problems.append(f"latest successful database backup is {age_days:.1f} days old (limit {max_age_days:g})")


def render_summary(report: Report, now: datetime, since: str) -> str:
    out = [f"## NBA watchdog: {'PROBLEMS' if report.problems else 'OK'} ({now:%Y-%m-%d %H:%MZ})", ""]
    if report.problems:
        out += ["### Problems", ""] + [f"- {p}" for p in report.problems] + [""]
    out += [f"### Snapshot coverage (games tipping off within 24 h either side; windows due since {since})", ""]
    if report.coverage:
        out += ["| Game | Tip-off (UTC) | " + " | ".join(LABELS) + " |", "|---|---|" + "---|" * len(LABELS)]
        for game in report.coverage:
            out.append(f"| {game['matchup']} ({game['game_id']}) | {game['tipoff']} | " + " | ".join(game["cells"].get(label, "-") for label in LABELS) + " |")
    else:
        out.append("No competitive games within 24 hours either side.")
    out += ["", f"Latest successful database backup: {report.latest_backup}", "", "### Evidence health", "", "```"] + report.health_lines + ["```", ""]
    return "\n".join(out)


def _read_csv(path: str) -> list[dict] | None:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return None


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--health", required=True, help="evidence-health output")
    p.add_argument("--health-exit", type=int, required=True)
    p.add_argument("--snapshots", required=True, help="expected_snapshots.sql CSV output")
    p.add_argument("--snapshots-exit", type=int, required=True)
    p.add_argument("--box-scores", required=True, help="box_scores.sql CSV output")
    p.add_argument("--box-scores-exit", type=int, required=True)
    p.add_argument("--backups", required=True, help="gh run list JSON output")
    p.add_argument("--backups-exit", type=int, required=True)
    p.add_argument("--backup-max-age-days", type=float, default=8.0)
    p.add_argument("--since", required=True, help="when snapshots were switched on (ISO 8601)")
    p.add_argument("--now", default=None, help="ISO 8601; default the wall clock (tests)")
    p.add_argument("--summary", required=True, help="markdown summary to write")
    p.add_argument("--problems", required=True, help="problem list to write, one per line (empty when healthy)")
    args = p.parse_args(argv)

    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = Report()
    check_health(_read_text(args.health), args.health_exit, report)
    check_snapshots(_read_csv(args.snapshots), args.snapshots_exit, report)
    check_box_scores(_read_csv(args.box_scores), args.box_scores_exit, report)
    check_backups(_read_json(args.backups), args.backups_exit, now, args.backup_max_age_days, report)
    with open(args.summary, "w", encoding="utf-8") as fh:
        fh.write(render_summary(report, now, args.since))
    with open(args.problems, "w", encoding="utf-8") as fh:
        fh.write("".join(f"{problem}\n" for problem in report.problems))
    print("\n".join(report.problems) or "no problems")
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
