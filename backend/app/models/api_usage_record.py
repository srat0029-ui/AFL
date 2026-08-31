"""Usage/request log for the external B2B pricing API — deliberately its
own standalone table with zero foreign key into any pricing table (match,
player, projection, snapshot, ...), so high-volume request logging can
never become entangled with the core pricing schema (item 3's explicit
requirement). This is also the source of truth `app/api_platform/
rate_limit.py` counts against — a rejected (429) request is still logged
here (with `rate_limited=True`), which is both correct (it really was a
request that arrived) and convenient (one table serves both usage
visibility and rate-limit enforcement, rather than two).

Never stores the API key, the Authorization header, or any request body —
only metadata about the request (item 15's "API keys never enter logs").
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNAVAILABLE = "unavailable"


class ApiUsageRecord(TimestampMixin, Base):
    __tablename__ = "api_usage_records"
    __table_args__ = (
        # Serves both rate-limit COUNT queries (per-consumer, recent window)
        # and usage-metrics aggregation - the two things this table exists for.
        Index("ix_api_usage_records_consumer_recorded", "consumer_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    consumer_id: Mapped[int | None] = mapped_column(ForeignKey("api_consumers.id"), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    freshness: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "fresh" | "stale" | "unavailable"
    rate_limited: Mapped[bool] = mapped_column(nullable=False, default=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<ApiUsageRecord {self.method} {self.endpoint} {self.status_code} consumer={self.consumer_id}>"
