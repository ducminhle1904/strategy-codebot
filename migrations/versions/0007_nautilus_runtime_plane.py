"""nautilus runtime control plane

Revision ID: 0007_nautilus_runtime_plane
Revises: 0006_backtest_hybrid_indexes
Create Date: 2026-06-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_nautilus_runtime_plane"
down_revision: str | None = "0006_backtest_hybrid_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nautilus_runtimes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("runtime_key", sa.String(length=512), nullable=False),
        sa.Column("broker_connection_id", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("risk_policy_id", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("strategy_ids_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("data_subscriptions_json", sa.JSON(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kill_switch_active", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('paper','live')", name="ck_nautilus_runtimes_mode"),
        sa.CheckConstraint(
            "state IN ('requested','provisioning','warming_up','running','degraded','stopping','stopped','failed')",
            name="ck_nautilus_runtimes_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "owner_user_id", "runtime_key", name="uq_nautilus_runtimes_tenant_key"),
    )
    op.create_index("ix_nautilus_runtimes_workspace_owner", "nautilus_runtimes", ["workspace_id", "owner_user_id"])
    op.create_index("ix_nautilus_runtimes_workspace_state", "nautilus_runtimes", ["workspace_id", "state"])
    op.create_index(op.f("ix_nautilus_runtimes_owner_user_id"), "nautilus_runtimes", ["owner_user_id"])
    op.create_index(op.f("ix_nautilus_runtimes_workspace_id"), "nautilus_runtimes", ["workspace_id"])

    op.create_table(
        "nautilus_runtime_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("runtime_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["runtime_id"], ["nautilus_runtimes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runtime_id", "sequence", name="uq_nautilus_runtime_events_runtime_sequence"),
    )
    op.create_index("ix_nautilus_runtime_events_runtime_sequence", "nautilus_runtime_events", ["runtime_id", "sequence"])
    op.create_index(
        "ix_nautilus_runtime_events_workspace_owner",
        "nautilus_runtime_events",
        ["workspace_id", "owner_user_id"],
    )
    op.create_index(op.f("ix_nautilus_runtime_events_owner_user_id"), "nautilus_runtime_events", ["owner_user_id"])
    op.create_index(op.f("ix_nautilus_runtime_events_runtime_id"), "nautilus_runtime_events", ["runtime_id"])
    op.create_index(op.f("ix_nautilus_runtime_events_workspace_id"), "nautilus_runtime_events", ["workspace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_nautilus_runtime_events_workspace_id"), table_name="nautilus_runtime_events")
    op.drop_index(op.f("ix_nautilus_runtime_events_runtime_id"), table_name="nautilus_runtime_events")
    op.drop_index(op.f("ix_nautilus_runtime_events_owner_user_id"), table_name="nautilus_runtime_events")
    op.drop_index("ix_nautilus_runtime_events_workspace_owner", table_name="nautilus_runtime_events")
    op.drop_index("ix_nautilus_runtime_events_runtime_sequence", table_name="nautilus_runtime_events")
    op.drop_table("nautilus_runtime_events")

    op.drop_index(op.f("ix_nautilus_runtimes_workspace_id"), table_name="nautilus_runtimes")
    op.drop_index(op.f("ix_nautilus_runtimes_owner_user_id"), table_name="nautilus_runtimes")
    op.drop_index("ix_nautilus_runtimes_workspace_state", table_name="nautilus_runtimes")
    op.drop_index("ix_nautilus_runtimes_workspace_owner", table_name="nautilus_runtimes")
    op.drop_table("nautilus_runtimes")
