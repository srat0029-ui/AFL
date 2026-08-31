"""Admin CLI tests. Monkeypatches app.api_platform.cli's SessionLocal
reference to the test's isolated in-memory db_session, exactly like
conftest.py's client fixture does for the request-logging middleware -
otherwise this CLI (correctly, for real usage) opens a session against the
real configured database."""

import pytest
from sqlalchemy import select

from app.api_platform import cli
from app.models import ApiConsumer, ApiKey


@pytest.fixture()
def cli_db(db_session, monkeypatch):
    # The CLI calls db.close() in its own finally block (correct for real
    # usage, since it owns a fresh session per invocation) - a test that
    # calls cli.main() more than once needs that close() to be a no-op so
    # the SAME db_session fixture instance stays usable across calls.
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", lambda: db_session)
    return db_session


class TestCreateConsumer:
    def test_creates_a_consumer_with_defaults(self, cli_db, capsys):
        code = cli.main(["create-consumer", "--name", "Acme Sportsbook"])
        assert code == 0
        consumer = cli_db.scalar(select(ApiConsumer).where(ApiConsumer.name == "Acme Sportsbook"))
        assert consumer is not None
        assert consumer.status == "active"
        assert "Created consumer" in capsys.readouterr().out

    def test_duplicate_name_is_rejected(self, cli_db, capsys):
        cli.main(["create-consumer", "--name", "Acme Sportsbook"])
        code = cli.main(["create-consumer", "--name", "Acme Sportsbook"])
        assert code == 1
        assert "already exists" in capsys.readouterr().out

    def test_custom_rate_limits_are_applied(self, cli_db):
        cli.main(["create-consumer", "--name", "Acme", "--rate-limit-per-minute", "10", "--daily-quota", "100"])
        consumer = cli_db.scalar(select(ApiConsumer).where(ApiConsumer.name == "Acme"))
        assert consumer.rate_limit_per_minute == 10
        assert consumer.daily_quota == 100


class TestCreateAndRevokeKey:
    def test_create_key_prints_the_raw_key_once(self, cli_db, capsys):
        cli.main(["create-consumer", "--name", "Acme"])
        code = cli.main(["create-key", "--consumer", "Acme"])
        assert code == 0
        output = capsys.readouterr().out
        assert "afl_" in output
        key = cli_db.scalar(select(ApiKey))
        assert key is not None
        assert key.status == "active"

    def test_create_key_for_unknown_consumer_fails(self, cli_db, capsys):
        code = cli.main(["create-key", "--consumer", "Nonexistent"])
        assert code == 1

    def test_revoke_key_by_prefix(self, cli_db, capsys):
        cli.main(["create-consumer", "--name", "Acme"])
        cli.main(["create-key", "--consumer", "Acme"])
        raw_key = [line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("afl_")][0].strip()
        prefix = raw_key[:12]

        code = cli.main(["revoke-key", "--key-prefix", prefix])

        assert code == 0
        key = cli_db.scalar(select(ApiKey))
        assert key.status == "revoked"
        assert key.revoked_at is not None

    def test_revoking_unknown_prefix_fails_cleanly(self, cli_db):
        code = cli.main(["revoke-key", "--key-prefix", "afl_nonexistent"])
        assert code == 1


class TestConsumerStatus:
    def test_disable_and_enable_consumer(self, cli_db):
        cli.main(["create-consumer", "--name", "Acme"])
        assert cli.main(["disable-consumer", "--name", "Acme"]) == 0
        consumer = cli_db.scalar(select(ApiConsumer).where(ApiConsumer.name == "Acme"))
        assert consumer.status == "disabled"

        assert cli.main(["enable-consumer", "--name", "Acme"]) == 0
        cli_db.refresh(consumer)
        assert consumer.status == "active"


class TestUsageCommand:
    def test_usage_with_no_records_does_not_crash(self, cli_db, capsys):
        cli.main(["create-consumer", "--name", "Acme"])
        code = cli.main(["usage", "--consumer", "Acme"])
        assert code == 0
        assert "0 request" in capsys.readouterr().out

    def test_usage_for_unknown_consumer_fails(self, cli_db):
        assert cli.main(["usage", "--consumer", "Nonexistent"]) == 1


def test_no_subcommand_prints_usage_and_returns_2(capsys):
    code = cli.main([])
    assert code == 2
    assert "Usage" in capsys.readouterr().out
