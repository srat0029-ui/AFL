"""Where a database's schema stands relative to the migrations in this
codebase. Read-only: it reads the database's alembic_version row and the
migration scripts on disk, and never changes either.

Used to refuse to run against a database that has not been migrated (so a
scheduled job fails clearly instead of half-working), and to show exactly
which migrations would be applied before anyone applies them.
"""

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@dataclass(frozen=True)
class MigrationStatus:
    database_revision: str | None  # None: no alembic_version table / row
    code_head: str
    pending: list[tuple[str, str]]  # (revision, description), oldest first
    is_current: bool


def _script_directory() -> ScriptDirectory:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return ScriptDirectory.from_config(config)


def migration_status(db: Session) -> MigrationStatus:
    scripts = _script_directory()
    head = scripts.get_current_head()
    bind = db.get_bind()
    revision = None
    if inspect(bind).has_table("alembic_version"):
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    pending: list[tuple[str, str]] = []
    if revision != head:
        for script in scripts.walk_revisions(base="base", head=head):  # newest first
            if script.revision == revision:
                break
            pending.append((script.revision, script.doc or ""))
        pending.reverse()
    return MigrationStatus(database_revision=revision, code_head=head, pending=pending, is_current=revision == head)
