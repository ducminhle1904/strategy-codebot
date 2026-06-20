"""observability feedback

Revision ID: 0002_observability_feedback
Revises: 0001_api_data_model
Create Date: 2026-06-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_observability_feedback"
down_revision: str | None = "0001_api_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_runs", sa.Column("request_id", sa.String(length=120), nullable=True))
    op.create_index(op.f("ix_assistant_runs_request_id"), "assistant_runs", ["request_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("message_id", sa.String(length=64), nullable=True),
        sa.Column("artifact_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("trace_id", sa.String(length=120), nullable=True),
        sa.Column("rating", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("correction", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('up','down','neutral')", name="ck_feedback_rating"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["conversation_messages.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_workspace_owner", "feedback", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_feedback_artifact_id"), "feedback", ["artifact_id"])
    op.create_index(op.f("ix_feedback_conversation_id"), "feedback", ["conversation_id"])
    op.create_index(op.f("ix_feedback_message_id"), "feedback", ["message_id"])
    op.create_index(op.f("ix_feedback_owner_user_id"), "feedback", ["owner_user_id"])
    op.create_index(op.f("ix_feedback_request_id"), "feedback", ["request_id"])
    op.create_index(op.f("ix_feedback_run_id"), "feedback", ["run_id"])
    op.create_index(op.f("ix_feedback_trace_id"), "feedback", ["trace_id"])
    op.create_index(op.f("ix_feedback_workspace_id"), "feedback", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_index(op.f("ix_assistant_runs_request_id"), table_name="assistant_runs")
    op.drop_column("assistant_runs", "request_id")
