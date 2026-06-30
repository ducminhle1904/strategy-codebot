"""add workflow task inbox

Revision ID: 0012_workflow_tasks
Revises: 0011_bot_proposals
Create Date: 2026-06-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0012_workflow_tasks"
down_revision: str | None = "0011_bot_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("workflow_id", sa.String(length=120), nullable=False),
        sa.Column("task_template_id", sa.String(length=120), nullable=False),
        sa.Column("step_id", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_user','blocked','completed','approved','rejected','cancelled')",
            name="ck_workflow_tasks_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "workflow_id",
            "task_template_id",
            name="uq_workflow_tasks_conversation_template",
        ),
    )
    op.create_index("ix_workflow_tasks_workspace_owner", "workflow_tasks", ["workspace_id", "owner_user_id"])
    op.create_index("ix_workflow_tasks_conversation_status", "workflow_tasks", ["conversation_id", "status"])
    op.create_index(op.f("ix_workflow_tasks_owner_user_id"), "workflow_tasks", ["owner_user_id"])
    op.create_index(op.f("ix_workflow_tasks_workspace_id"), "workflow_tasks", ["workspace_id"])
    op.create_index(op.f("ix_workflow_tasks_conversation_id"), "workflow_tasks", ["conversation_id"])
    op.create_index(op.f("ix_workflow_tasks_run_id"), "workflow_tasks", ["run_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_tasks_run_id"), table_name="workflow_tasks")
    op.drop_index(op.f("ix_workflow_tasks_conversation_id"), table_name="workflow_tasks")
    op.drop_index(op.f("ix_workflow_tasks_workspace_id"), table_name="workflow_tasks")
    op.drop_index(op.f("ix_workflow_tasks_owner_user_id"), table_name="workflow_tasks")
    op.drop_index("ix_workflow_tasks_conversation_status", table_name="workflow_tasks")
    op.drop_index("ix_workflow_tasks_workspace_owner", table_name="workflow_tasks")
    op.drop_table("workflow_tasks")
