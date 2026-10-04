"""Frozen V1.5 outlook snapshots, the canonical serving bundle, and read-only
operational checks.

    python -m app.nba.outlooks.cli snapshot                          # produce every due snapshot now
    python -m app.nba.outlooks.cli snapshot --dry-run                # compute, print and measure; write nothing
    python -m app.nba.outlooks.cli snapshot --dry-run --labels T-372h  # custom label: dry runs only
    python -m app.nba.outlooks.cli verify-serving-models             # bundle present, unmodified, golden predictions (exit 1 if not)
    python -m app.nba.outlooks.cli import-serving-models             # write the canonical serving runs into this DB's registry
    python -m app.nba.outlooks.cli registry                          # list serving runs in this DB (read-only)
    python -m app.nba.outlooks.cli db-size                           # database size and largest tables (read-only)
"""

import argparse
import json
import sys

from app.database import SessionLocal


def _print_dry_run(result, rows_per_game: int) -> None:
    for item in result.produced + result.recorded_without_rows:
        print(f"--- game {item['game_id']} {item['label']}: {item['status']}  {item.get('reason') or ''}")
        if "evidence_freshness" in item:
            print("    evidence_freshness: " + json.dumps(item["evidence_freshness"], default=str))
        for r in item.get("rows_preview", [])[:rows_per_game]:
            m = r["expected_minutes_if_plays"]
            q = r["quantiles"] or {}
            print(
                f"    team {r['team_id']} player {r['player_id']}: P(play) {r['p_play']:.3f}  P(10+) {r['p_rotation']:.3f}  "
                f"E(min|play) {'-' if m is None else f'{m:.1f}'}  p10-p90 {q.get('p10', '-')}-{q.get('p90', '-')}  "
                f"tier {r['tier']}  roster_listed {r['roster_listed']}  availability {r['availability_evidence'].get('state')}"
                f"{' ' + str(r['availability_evidence'].get('status')) if r['availability_evidence'].get('status') else ''}"
                f"  rules {list(r['experimental_rules'])}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nba.outlooks.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot", help="freeze every snapshot that is due now")
    p.add_argument("--labels", default=None, help="comma-separated, e.g. T-24h,T-4h,T-1h,T-30m (default: those four); other labels only with --dry-run")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rows-per-game", type=int, default=8, help="dry run: example rows printed per game")
    sub.add_parser("verify-serving-models", help="verify the bundled canonical models (no database needed)")
    sub.add_parser("import-serving-models", help="import the canonical serving runs into this database's registry")
    sub.add_parser("registry", help="list the serving runs in this database (read-only)")
    sub.add_parser("db-size", help="database size and largest tables (read-only)")
    args = parser.parse_args(argv)

    if args.command == "verify-serving-models":
        from app.nba.serving_bundle import verify_bundle

        result = verify_bundle()
        print("\n".join(result.lines))
        return 0 if result.ok else 1

    if args.command == "import-serving-models":
        from app.nba.serving_bundle import import_bundle

        with SessionLocal() as db:
            print("\n".join(import_bundle(db)))
        return 0

    if args.command == "registry":
        from sqlalchemy import select

        from app.models.nba import NbaMinutesModelRun

        with SessionLocal() as db:
            for run in db.scalars(select(NbaMinutesModelRun).where(NbaMinutesModelRun.purpose == "serving").order_by(NbaMinutesModelRun.id)).all():
                print(f"{run.id:>4} {run.model_version:<14} {run.model_name:<22} sha256 {run.artifact_sha256 or '-'}  path {run.artifact_path or '-'}  run_key {run.run_key}")
        return 0

    if args.command == "db-size":
        from app.nba.outlooks.ops import database_size

        with SessionLocal() as db:
            print("\n".join(database_size(db)))
        return 0

    from app.nba.outlooks.ops import measured
    from app.nba.outlooks.snapshots import DEFAULT_SNAPSHOTS, parse_specs, run_due_snapshots

    specs = parse_specs(args.labels) if args.labels else DEFAULT_SNAPSHOTS
    if not args.dry_run and {s.label for s in specs} - {s.label for s in DEFAULT_SNAPSHOTS}:
        parser.error("custom snapshot labels are allowed only with --dry-run (frozen snapshots use the standard labels)")
    with measured("snapshot" + (" dry run" if args.dry_run else "")):
        with SessionLocal() as db:
            result = run_due_snapshots(db, specs=specs, dry_run=args.dry_run)
    print(result.summary())
    if args.dry_run:
        _print_dry_run(result, args.rows_per_game)
    return 0


if __name__ == "__main__":
    sys.exit(main())
