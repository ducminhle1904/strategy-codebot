"""harness route health

Revision ID: 0003_harness_route_health
Revises: 0002_observability_feedback
Create Date: 2026-06-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_harness_route_health"
down_revision: str | None = "0002_observability_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "harness_route_health",
        sa.Column("user_tier", sa.Text(), nullable=False),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("route_model", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("gateway", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("slow_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cooldown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failure_max", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("max_latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "recent_latency_ms",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("last_failure_class", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_tier", "workflow", "stage", "route_model", "gateway"),
    )
    op.create_index("ix_harness_route_health_cooldown_until", "harness_route_health", ["cooldown_until"])


def downgrade() -> None:
    op.drop_index("ix_harness_route_health_cooldown_until", table_name="harness_route_health")
    op.drop_table("harness_route_health")
