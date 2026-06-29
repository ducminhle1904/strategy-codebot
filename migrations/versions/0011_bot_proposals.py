"""add bot proposals

Revision ID: 0011_bot_proposals
Revises: 0010_nautilus_warming_up_state
Create Date: 2026-06-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011_bot_proposals"
down_revision: str | None = "0010_nautilus_warming_up_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_proposals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=64), nullable=True),
        sa.Column("source_run_id", sa.String(length=64), nullable=True),
        sa.Column("source_artifact_ids_json", sa.JSON(), nullable=False),
        sa.Column("strategy_id", sa.String(length=160), nullable=False),
        sa.Column("strategy_name", sa.String(length=240), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("data_subscriptions_json", sa.JSON(), nullable=False),
        sa.Column("broker_connection_id", sa.String(length=120), nullable=True),
        sa.Column("account_id", sa.String(length=120), nullable=True),
        sa.Column("risk_policy_id", sa.String(length=120), nullable=True),
        sa.Column("readiness_checks_json", sa.JSON(), nullable=False),
        sa.Column("missing_inputs_json", sa.JSON(), nullable=False),
        sa.Column("runtime_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','missing_inputs','ready','rejected','started')",
            name="ck_bot_proposals_status",
        ),
        sa.ForeignKeyConstraint(["runtime_id"], ["nautilus_runtimes.id"]),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bot_proposals_workspace_owner", "bot_proposals", ["workspace_id", "owner_user_id"])
    op.create_index("ix_bot_proposals_workspace_status", "bot_proposals", ["workspace_id", "status"])
    op.create_index("ix_bot_proposals_runtime_id", "bot_proposals", ["runtime_id"])
    op.create_index(op.f("ix_bot_proposals_owner_user_id"), "bot_proposals", ["owner_user_id"])
    op.create_index(op.f("ix_bot_proposals_workspace_id"), "bot_proposals", ["workspace_id"])
    op.create_index(op.f("ix_bot_proposals_source_conversation_id"), "bot_proposals", ["source_conversation_id"])
    op.create_index(op.f("ix_bot_proposals_source_run_id"), "bot_proposals", ["source_run_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_proposals_source_run_id"), table_name="bot_proposals")
    op.drop_index(op.f("ix_bot_proposals_source_conversation_id"), table_name="bot_proposals")
    op.drop_index(op.f("ix_bot_proposals_workspace_id"), table_name="bot_proposals")
    op.drop_index(op.f("ix_bot_proposals_owner_user_id"), table_name="bot_proposals")
    op.drop_index("ix_bot_proposals_runtime_id", table_name="bot_proposals")
    op.drop_index("ix_bot_proposals_workspace_status", table_name="bot_proposals")
    op.drop_index("ix_bot_proposals_workspace_owner", table_name="bot_proposals")
    op.drop_table("bot_proposals")
