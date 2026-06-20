"""conversation memory

Revision ID: 0004_conversation_memory
Revises: 0003_harness_route_health
Create Date: 2026-06-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_conversation_memory"
down_revision: str | None = "0003_harness_route_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("covered_message_id", sa.String(length=64), nullable=True),
        sa.Column("summary_version", sa.Integer(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["covered_message_id"], ["conversation_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_memories_conversation"),
    )
    op.create_index("ix_conversation_memories_workspace_owner", "conversation_memories", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_conversation_memories_conversation_id"), "conversation_memories", ["conversation_id"])
    op.create_index(op.f("ix_conversation_memories_covered_message_id"), "conversation_memories", ["covered_message_id"])
    op.create_index(op.f("ix_conversation_memories_owner_user_id"), "conversation_memories", ["owner_user_id"])
    op.create_index(op.f("ix_conversation_memories_workspace_id"), "conversation_memories", ["workspace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_memories_workspace_id"), table_name="conversation_memories")
    op.drop_index(op.f("ix_conversation_memories_owner_user_id"), table_name="conversation_memories")
    op.drop_index(op.f("ix_conversation_memories_covered_message_id"), table_name="conversation_memories")
    op.drop_index(op.f("ix_conversation_memories_conversation_id"), table_name="conversation_memories")
    op.drop_index("ix_conversation_memories_workspace_owner", table_name="conversation_memories")
    op.drop_table("conversation_memories")
