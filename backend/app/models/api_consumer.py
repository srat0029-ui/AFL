"""API consumer + key management for the external B2B pricing API
(app/api_platform/). A "consumer" is a named external client (a sportsbook,
trading platform, or downstream service); a consumer can hold multiple keys
(rotation without downtime — issue a new key, revoke the old one once the
consumer has switched over).

Keys are never stored in plaintext. Only a SHA-256 hash of the full random
token is persisted (`key_hash`) — a high-entropy random token doesn't need a
slow password-hashing KDF (bcrypt/argon2 exist to slow down brute-forcing a
LOW-entropy human password; a 32-byte random token has no such weakness to
compensate for). `key_prefix` (the first 12 characters of the raw token) is
stored purely so an admin listing can show "key starting with afl_a1b2c3d4"
without ever being able to reconstruct or re-display the full value — the
full key is only ever shown once, at creation time, by
app/api_platform/cli.py's `create-key` command.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

CONSUMER_STATUS_ACTIVE = "active"
CONSUMER_STATUS_DISABLED = "disabled"

KEY_STATUS_ACTIVE = "active"
KEY_STATUS_REVOKED = "revoked"

DEFAULT_RATE_LIMIT_PER_MINUTE = 60
DEFAULT_DAILY_QUOTA = 5_000


class ApiConsumer(TimestampMixin, Base):
    __tablename__ = "api_consumers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CONSUMER_STATUS_ACTIVE)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_RATE_LIMIT_PER_MINUTE)
    daily_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_DAILY_QUOTA)

    keys: Mapped[list["ApiKey"]] = relationship(back_populates="consumer", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ApiConsumer {self.name} status={self.status}>"


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_key_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("api_consumers.id"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # sha256 hexdigest, 64 chars
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=KEY_STATUS_ACTIVE)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    consumer: Mapped["ApiConsumer"] = relationship(back_populates="keys")

    def __repr__(self) -> str:
        return f"<ApiKey {self.key_prefix}... consumer={self.consumer_id} status={self.status}>"
