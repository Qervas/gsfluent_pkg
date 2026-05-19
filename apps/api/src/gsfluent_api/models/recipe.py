"""Recipe entity + append-only RecipeVersion history. Spec §5.2."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, SoftDeleteMixin, TimestampMixin


class Recipe(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "recipes"

    name: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    starred: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    versions: Mapped[list["RecipeVersion"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeVersion.version",
    )


class RecipeVersion(Base, IdMixin, TimestampMixin):
    """Snapshot of recipe content per version. Appended on every PATCH."""

    __tablename__ = "recipe_versions"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(nullable=False)

    recipe: Mapped[Recipe] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("recipe_id", "version", name="uq_recipe_version"),)
