"""initial schema — 6 tables, 3 enums

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


run_status = sa.Enum(
    "queued", "running", "completed", "failed", "cancelled",
    name="run_status",
    create_type=False,
)
artifact_kind = sa.Enum(
    "cell", "log", "video", "preview", "manifest",
    name="artifact_kind",
    create_type=False,
)
render_session_status = sa.Enum(
    "signaling", "active", "closed", "failed",
    name="render_session_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # Postgres enum types — created once, referenced by name from tables.
    sa.Enum(
        "queued", "running", "completed", "failed", "cancelled",
        name="run_status",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "cell", "log", "video", "preview", "manifest",
        name="artifact_kind",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "signaling", "active", "closed", "failed",
        name="render_session_status",
    ).create(bind, checkfirst=True)

    op.create_table(
        "models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("minio_path", sa.String, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("num_gaussians", sa.Integer, nullable=True),
        sa.Column("source_metadata", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "recipes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("starred", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
    )

    op.create_table(
        "recipe_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("recipe_id", UUID(as_uuid=True),
                  sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.UniqueConstraint("recipe_id", "version", name="uq_recipe_version"),
    )
    op.create_index("ix_recipe_versions_recipe_id", "recipe_versions", ["recipe_id"])

    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="queued"),
        sa.Column("model_id", UUID(as_uuid=True),
                  sa.ForeignKey("models.id"), nullable=False),
        sa.Column("recipe_id", UUID(as_uuid=True),
                  sa.ForeignKey("recipes.id"), nullable=True),
        sa.Column("recipe_snapshot", JSONB, nullable=False),
        sa.Column("worker_id", sa.String, nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gpu_seconds", sa.Numeric(12, 3), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("peak_vram_bytes", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    op.create_index("ix_runs_model_id", "runs", ["model_id"])
    op.create_index("ix_runs_recipe_id", "runs", ["recipe_id"])
    op.create_index("ix_runs_status_created", "runs", ["status", "created_at"])
    op.create_index("ix_runs_idempotency_key", "runs", ["idempotency_key"])

    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", artifact_kind, nullable=False),
        sa.Column("frame_idx", sa.Integer, nullable=True),
        sa.Column("minio_path", sa.String, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_artifacts_run_kind_frame",
                    "artifacts", ["run_id", "kind", "frame_idx"])

    op.create_table(
        "render_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.func.gen_random_uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("model_id", UUID(as_uuid=True),
                  sa.ForeignKey("models.id"), nullable=True),
        sa.Column("worker_id", sa.String, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gpu_seconds", sa.Numeric(12, 3), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("bytes_streamed", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("status", render_session_status, nullable=False,
                  server_default="signaling"),
        sa.CheckConstraint(
            "(run_id IS NULL) <> (model_id IS NULL)",
            name="ck_render_session_target",
        ),
    )
    op.create_index("ix_render_sessions_run_id", "render_sessions", ["run_id"])
    op.create_index("ix_render_sessions_model_id", "render_sessions", ["model_id"])


def downgrade() -> None:
    op.drop_table("render_sessions")
    op.drop_table("artifacts")
    op.drop_table("runs")
    op.drop_table("recipe_versions")
    op.drop_table("recipes")
    op.drop_table("models")

    bind = op.get_bind()
    sa.Enum(name="render_session_status").drop(bind, checkfirst=True)
    sa.Enum(name="artifact_kind").drop(bind, checkfirst=True)
    sa.Enum(name="run_status").drop(bind, checkfirst=True)
