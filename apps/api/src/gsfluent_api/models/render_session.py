"""RenderSession entity — one active WebRTC stream. Spec §5.5."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin
from .enums import RenderSessionStatus


class RenderSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "render_sessions"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id"),
        nullable=True,
        index=True,
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("models.id"),
        nullable=True,
        index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    ended_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gpu_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False, server_default=text("0")
    )
    bytes_streamed: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    status: Mapped[RenderSessionStatus] = mapped_column(
        Enum(
            RenderSessionStatus,
            name="render_session_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=RenderSessionStatus.signaling.value,
    )

    __table_args__ = (
        # Spec §5.5: exactly one of run_id / model_id must be non-null.
        CheckConstraint(
            "(run_id IS NULL) <> (model_id IS NULL)",
            name="ck_render_session_target",
        ),
    )
