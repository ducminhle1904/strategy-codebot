"""nautilus paper worker plane

Revision ID: 0009_nautilus_worker_plane
Revises: 0008_nautilus_runtime_health
Create Date: 2026-06-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_nautilus_worker_plane"
down_revision: str | None = "0008_nautilus_runtime_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nautilus_runtimes",
        sa.Column("desired_state", sa.String(length=32), nullable=False, server_default="running"),
    )
    op.add_column("nautilus_runtimes", sa.Column("worker_id", sa.String(length=120), nullable=True))
    op.add_column("nautilus_runtimes", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "nautilus_runtimes",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("nautilus_runtimes", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nautilus_runtimes", sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nautilus_runtimes", sa.Column("last_error_json", sa.JSON(), nullable=True))
    op.add_column("nautilus_runtimes", sa.Column("stream_cursor_json", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_nautilus_runtimes_worker_id"), "nautilus_runtimes", ["worker_id"])
    op.create_index(
        "ix_nautilus_runtimes_desired_lease",
        "nautilus_runtimes",
        ["mode", "desired_state", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_nautilus_runtimes_desired_lease", table_name="nautilus_runtimes")
    op.drop_index(op.f("ix_nautilus_runtimes_worker_id"), table_name="nautilus_runtimes")
    op.drop_column("nautilus_runtimes", "stream_cursor_json")
    op.drop_column("nautilus_runtimes", "last_error_json")
    op.drop_column("nautilus_runtimes", "stopped_at")
    op.drop_column("nautilus_runtimes", "started_at")
    op.drop_column("nautilus_runtimes", "generation")
    op.drop_column("nautilus_runtimes", "lease_until")
    op.drop_column("nautilus_runtimes", "worker_id")
    op.drop_column("nautilus_runtimes", "desired_state")
