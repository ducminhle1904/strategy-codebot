"""nautilus runtime heartbeat idempotency

Revision ID: 0008_nautilus_runtime_health
Revises: 0007_nautilus_runtime_plane
Create Date: 2026-06-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_nautilus_runtime_health"
down_revision: str | None = "0007_nautilus_runtime_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nautilus_runtimes", sa.Column("heartbeat_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("nautilus_runtimes", sa.Column("heartbeat_metrics_json", sa.JSON(), nullable=True))
    op.add_column("nautilus_runtimes", sa.Column("last_heartbeat_event_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("nautilus_runtime_events", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    op.create_index(
        op.f("ix_nautilus_runtime_events_idempotency_key"),
        "nautilus_runtime_events",
        ["idempotency_key"],
    )
    op.create_index(
        "uq_nautilus_runtime_events_runtime_idempotency",
        "nautilus_runtime_events",
        ["runtime_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_nautilus_runtime_events_runtime_idempotency",
        table_name="nautilus_runtime_events",
    )
    op.drop_index(op.f("ix_nautilus_runtime_events_idempotency_key"), table_name="nautilus_runtime_events")
    op.drop_column("nautilus_runtime_events", "idempotency_key")

    op.drop_column("nautilus_runtimes", "last_heartbeat_event_at")
    op.drop_column("nautilus_runtimes", "heartbeat_metrics_json")
    op.drop_column("nautilus_runtimes", "heartbeat_count")
