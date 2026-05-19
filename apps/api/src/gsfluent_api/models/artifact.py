"""Artifact entity — output of a run (cell / log / video / preview / manifest). Spec §5.4."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin
from .enums import ArtifactKind

if TYPE_CHECKING:
    from .run import Run


class Artifact(Base, IdMixin, TimestampMixin):
    __tablename__ = "artifacts"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        Enum(
            ArtifactKind,
            name="artifact_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    frame_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minio_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    run: Mapped["Run"] = relationship(back_populates="artifacts")

    __table_args__ = (
        # Common query: artifacts for a run, optionally filtered by kind / ordered by frame.
        Index("ix_artifacts_run_kind_frame", "run_id", "kind", "frame_idx"),
    )
