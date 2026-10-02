"""Prospective-integrity primitives shared across sports.

The AFL application's strongest property is its prospective boundary: a
price is frozen before the outcome exists, settled exactly once, and never
overwritten (see PricingSnapshot / PropMarketObservation). There that
property is upheld by discipline — every write path is careful. This module
turns the same rules into things the code enforces:

1. `ensure_information_available` — nothing stamped as learned AFTER an
   information cutoff may feed a prediction made at that cutoff.
2. `protect_frozen_record` — an ORM-level guard: a protected row's frozen
   columns can never change after insert, its late columns (closing line,
   settlement) can each be written exactly once, and the row can never be
   deleted through the ORM.

Scope of the guard, stated honestly: it covers ORM unit-of-work flushes
(session.add / attribute assignment / session.delete). A bulk
`update()`/`delete()` statement or raw SQL bypasses ORM events entirely —
application code must not use those against protected tables. A database
trigger would close that gap and is the natural next hardening step on
Postgres; it is not done here.

AFL's existing tables are deliberately NOT retrofitted with the guard in
this change — they have a working, tested write discipline and one-off
maintenance scripts whose behaviour should not be altered as a side effect
of adding a second sport.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, select
from sqlalchemy import inspect as orm_inspect
from sqlalchemy.orm import Mapper


class InformationLeakError(ValueError):
    """Information stamped after the cutoff was offered to a prediction made
    at that cutoff."""


class FrozenRecordError(RuntimeError):
    """An attempt to change a frozen column, rewrite an already-written
    late column, or delete a protected row."""


def ensure_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes even for timezone=True columns;
    Postgres returns aware ones. Every timestamp this project writes is UTC,
    so a naive value read back is UTC by construction."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ensure_information_available(known_at: datetime, cutoff: datetime, *, what: str) -> None:
    """Raise unless `known_at` (when this system actually held the piece of
    information) is at or before `cutoff`."""
    if ensure_utc(known_at) > ensure_utc(cutoff):
        raise InformationLeakError(
            f"{what} was learned at {ensure_utc(known_at).isoformat()}, after the information cutoff {ensure_utc(cutoff).isoformat()}"
        )


def _comparable(value: Any) -> Any:
    return ensure_utc(value) if isinstance(value, datetime) else value


def protect_frozen_record(model: type, *, write_once_groups: dict[str, tuple[str, ...]] | None = None) -> None:
    """Make `model` append-only with optional write-once column groups.

    `write_once_groups` maps a MARKER column to the columns written together
    with it, e.g. {"settled_at": ("settled_at", "actual_value", "outcome")}.
    Columns in a group may be assigned only while the persisted marker is
    still NULL — so the whole group is written at most once, and a group
    written with some members left NULL cannot be topped up later. Every
    column outside all groups is frozen at insert.

    The check compares against the PERSISTED row, not session attribute
    history, so it holds even when the instance was expired or never fully
    loaded.
    """
    groups = write_once_groups or {}
    marker_by_column = {column: marker for marker, columns in groups.items() for column in columns}

    @event.listens_for(model, "before_update")
    def _reject_frozen_changes(mapper: Mapper, connection, target) -> None:
        state = orm_inspect(target)
        changed = [attr.key for attr in mapper.column_attrs if attr.key != "updated_at" and state.attrs[attr.key].history.has_changes()]
        if not changed:
            return
        table = mapper.local_table
        pk_filter = [column == getattr(target, mapper.get_property_by_column(column).key) for column in mapper.primary_key]
        persisted = connection.execute(select(table).where(*pk_filter)).mappings().one()
        for key in changed:
            column_name = mapper.column_attrs[key].columns[0].name
            if _comparable(persisted[column_name]) == _comparable(getattr(target, key)):
                continue
            marker = marker_by_column.get(key)
            if marker is None:
                raise FrozenRecordError(f"{model.__name__}.{key} is frozen at insert and cannot be changed")
            if persisted[mapper.column_attrs[marker].columns[0].name] is not None:
                raise FrozenRecordError(f"{model.__name__}.{key} belongs to a write-once group already written (marker {marker!r} is set)")

    @event.listens_for(model, "before_delete")
    def _reject_delete(mapper: Mapper, connection, target) -> None:
        raise FrozenRecordError(f"{model.__name__} rows are append-only and cannot be deleted")
