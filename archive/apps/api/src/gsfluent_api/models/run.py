"""Run entity — one execution of a recipe on a model. Spec §5.3."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin
from .enums import RunStatus

if TYPE_CHECKING:
    from .artifact import Artifact


class Run(Base, IdMixin, TimestampMixin):
    __tablename__ = "runs"

    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        server_default=RunStatus.queued.value,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("models.id"),
        nullable=False,
        index=True,
    )
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id"),
        nullable=True,
        index=True,
    )
    recipe_snapshot: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    queued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gpu_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False, server_default=text("0")
    )
    peak_vram_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )

    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Fast filter by status; partial index on the hot path.
        Index("ix_runs_status_created", "status", "created_at"),
    )
