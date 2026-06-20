"""run jobs queue

Revision ID: 0005_run_jobs
Revises: 0004_conversation_memory
Create Date: 2026-06-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_run_jobs"
down_revision: str | None = "0004_conversation_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_run_jobs_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_jobs_status_type_created", "run_jobs", ["status", "job_type", "created_at"])
    op.create_index("ix_run_jobs_workspace_status", "run_jobs", ["workspace_id", "status"])
    op.create_index(op.f("ix_run_jobs_job_type"), "run_jobs", ["job_type"])
    op.create_index(op.f("ix_run_jobs_lease_owner"), "run_jobs", ["lease_owner"])
    op.create_index(op.f("ix_run_jobs_leased_until"), "run_jobs", ["leased_until"])
    op.create_index(op.f("ix_run_jobs_owner_user_id"), "run_jobs", ["owner_user_id"])
    op.create_index(op.f("ix_run_jobs_run_id"), "run_jobs", ["run_id"])
    op.create_index(op.f("ix_run_jobs_workspace_id"), "run_jobs", ["workspace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_run_jobs_workspace_id"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_run_id"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_owner_user_id"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_leased_until"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_lease_owner"), table_name="run_jobs")
    op.drop_index(op.f("ix_run_jobs_job_type"), table_name="run_jobs")
    op.drop_index("ix_run_jobs_workspace_status", table_name="run_jobs")
    op.drop_index("ix_run_jobs_status_type_created", table_name="run_jobs")
    op.drop_table("run_jobs")
