"""Model entity — a 3DGS asset uploaded by the team. Spec §5.1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, SoftDeleteMixin, TimestampMixin


class Model(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "models"

    name: Mapped[str] = mapped_column(String, nullable=False)
    minio_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    num_gaussians: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
