"""Admin CLI for API consumer/key management — CLI-only by design (see
app/api_platform/__init__.py's module docstring for why this isn't an HTTP
admin API: it would need its own auth story, which the CLI avoids
entirely, matching how every other operational workflow in this repo is
already CLI-driven, not UI-driven).

Usage:
    python -m app.api_platform.cli create-consumer --name "Acme Sportsbook"
    python -m app.api_platform.cli create-key --consumer "Acme Sportsbook"
    python -m app.api_platform.cli revoke-key --key-prefix afl_a1b2c3d4
    python -m app.api_platform.cli disable-consumer --name "Acme Sportsbook"
    python -m app.api_platform.cli enable-consumer --name "Acme Sportsbook"
    python -m app.api_platform.cli usage --consumer "Acme Sportsbook" --hours 24

Follows app/player_modelling/cli.py's argv[0]-dispatch style — the only
existing multi-subcommand CLI convention in this codebase.
"""

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.api_platform.keys import generate_api_key, hash_key, key_prefix as compute_key_prefix
from app.database import SessionLocal
from app.models import (
    ApiConsumer,
    ApiKey,
    ApiUsageRecord,
    CONSUMER_STATUS_ACTIVE,
    CONSUMER_STATUS_DISABLED,
    DEFAULT_DAILY_QUOTA,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    KEY_STATUS_ACTIVE,
    KEY_STATUS_REVOKED,
)

_SUBCOMMANDS = ("create-consumer", "create-key", "revoke-key", "disable-consumer", "enable-consumer", "usage")


def _flag(argv: list[str], name: str, default: str | None = None) -> str | None:
    if name in argv:
        idx = argv.index(name)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return default


def _create_consumer(argv: list[str]) -> int:
    name = _flag(argv, "--name")
    if not name:
        print("Usage: create-consumer --name <name> [--rate-limit-per-minute N] [--daily-quota N]")
        return 2
    rate_limit = int(_flag(argv, "--rate-limit-per-minute", str(DEFAULT_RATE_LIMIT_PER_MINUTE)))
    daily_quota = int(_flag(argv, "--daily-quota", str(DEFAULT_DAILY_QUOTA)))

    db = SessionLocal()
    try:
        existing = db.scalar(select(ApiConsumer).where(ApiConsumer.name == name))
        if existing is not None:
            print(f"A consumer named {name!r} already exists (id={existing.id}).")
            return 1
        consumer = ApiConsumer(name=name, status=CONSUMER_STATUS_ACTIVE, rate_limit_per_minute=rate_limit, daily_quota=daily_quota)
        db.add(consumer)
        db.commit()
        db.refresh(consumer)
        print(f"Created consumer {name!r} (id={consumer.id}, rate_limit_per_minute={rate_limit}, daily_quota={daily_quota}).")
        print("Next: python -m app.api_platform.cli create-key --consumer " + f"\"{name}\"")
        return 0
    finally:
        db.close()


def _create_key(argv: list[str]) -> int:
    consumer_name = _flag(argv, "--consumer")
    if not consumer_name:
        print("Usage: create-key --consumer <name>")
        return 2

    db = SessionLocal()
    try:
        consumer = db.scalar(select(ApiConsumer).where(ApiConsumer.name == consumer_name))
        if consumer is None:
            print(f"No consumer named {consumer_name!r}. Create one first with create-consumer.")
            return 1
        raw_key = generate_api_key()
        db.add(ApiKey(consumer_id=consumer.id, key_hash=hash_key(raw_key), key_prefix=compute_key_prefix(raw_key), status=KEY_STATUS_ACTIVE))
        db.commit()
        print(f"New API key for {consumer_name!r} (shown once - it is not recoverable after this):")
        print(f"  {raw_key}")
        print(f"Prefix for future reference: {compute_key_prefix(raw_key)}")
        return 0
    finally:
        db.close()


def _revoke_key(argv: list[str]) -> int:
    prefix = _flag(argv, "--key-prefix")
    if not prefix:
        print("Usage: revoke-key --key-prefix <prefix>")
        return 2

    db = SessionLocal()
    try:
        matches = db.scalars(select(ApiKey).where(ApiKey.key_prefix == prefix, ApiKey.status == KEY_STATUS_ACTIVE)).all()
        if not matches:
            print(f"No active key with prefix {prefix!r}.")
            return 1
        for key in matches:
            key.status = KEY_STATUS_REVOKED
            key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        print(f"Revoked {len(matches)} key(s) with prefix {prefix!r}.")
        return 0
    finally:
        db.close()


def _set_consumer_status(argv: list[str], status: str) -> int:
    name = _flag(argv, "--name")
    if not name:
        print(f"Usage: {'disable' if status == CONSUMER_STATUS_DISABLED else 'enable'}-consumer --name <name>")
        return 2

    db = SessionLocal()
    try:
        consumer = db.scalar(select(ApiConsumer).where(ApiConsumer.name == name))
        if consumer is None:
            print(f"No consumer named {name!r}.")
            return 1
        consumer.status = status
        db.commit()
        print(f"Consumer {name!r} is now {status}.")
        return 0
    finally:
        db.close()


def _usage(argv: list[str]) -> int:
    consumer_name = _flag(argv, "--consumer")
    hours = int(_flag(argv, "--hours", "24"))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    db = SessionLocal()
    try:
        query = select(ApiUsageRecord).where(ApiUsageRecord.recorded_at >= since)
        consumer = None
        if consumer_name:
            consumer = db.scalar(select(ApiConsumer).where(ApiConsumer.name == consumer_name))
            if consumer is None:
                print(f"No consumer named {consumer_name!r}.")
                return 1
            query = query.where(ApiUsageRecord.consumer_id == consumer.id)
        records = db.scalars(query).all()

        label = consumer_name or "ALL CONSUMERS"
        print(f"Usage for {label} over the last {hours}h ({len(records)} request(s)):")
        if not records:
            return 0

        n_success = sum(1 for r in records if 200 <= r.status_code < 400)
        n_errors = len(records) - n_success
        n_rate_limited = sum(1 for r in records if r.rate_limited)
        latencies = sorted(r.latency_ms for r in records)

        def _pct(p: float) -> float:
            idx = min(int(len(latencies) * p), len(latencies) - 1)
            return latencies[idx]

        print(f"  Success rate: {n_success / len(records):.1%}  ({n_success} ok / {n_errors} error)")
        print(f"  Rate-limit rejections: {n_rate_limited}")
        print(f"  Latency p50/p95: {_pct(0.50):.1f}ms / {_pct(0.95):.1f}ms")

        by_endpoint: dict[str, int] = {}
        for r in records:
            by_endpoint[r.endpoint] = by_endpoint.get(r.endpoint, 0) + 1
        print("  Requests by endpoint:")
        for endpoint, count in sorted(by_endpoint.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>5}  {endpoint}")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in _SUBCOMMANDS:
        print(f"Usage: python -m app.api_platform.cli <{'|'.join(_SUBCOMMANDS)}> [options]")
        return 2

    command, rest = argv[0], argv[1:]
    if command == "create-consumer":
        return _create_consumer(rest)
    if command == "create-key":
        return _create_key(rest)
    if command == "revoke-key":
        return _revoke_key(rest)
    if command == "disable-consumer":
        return _set_consumer_status(rest, CONSUMER_STATUS_DISABLED)
    if command == "enable-consumer":
        return _set_consumer_status(rest, CONSUMER_STATUS_ACTIVE)
    if command == "usage":
        return _usage(rest)
    return 2


if __name__ == "__main__":
    sys.exit(main())
