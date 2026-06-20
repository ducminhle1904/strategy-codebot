"""api data model

Revision ID: 0001_api_data_model
Revises:
Create Date: 2026-06-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_api_data_model"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("external_subject", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("external_subject"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner','admin','member')", name="ck_workspace_memberships_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
    )
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('strategy_design','pine_generation','mql5_design','review','validation','education')",
            name="ck_conversation_threads_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_threads_workspace_owner", "conversation_threads", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_conversation_threads_owner_user_id"), "conversation_threads", ["owner_user_id"])
    op.create_index(op.f("ix_conversation_threads_workspace_id"), "conversation_threads", ["workspace_id"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('user','assistant','system','tool')", name="ck_conversation_messages_role"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_workspace_owner", "conversation_messages", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_conversation_messages_conversation_id"), "conversation_messages", ["conversation_id"])
    op.create_index(op.f("ix_conversation_messages_owner_user_id"), "conversation_messages", ["owner_user_id"])
    op.create_index(op.f("ix_conversation_messages_workspace_id"), "conversation_messages", ["workspace_id"])
    op.create_table(
        "assistant_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("retry_of_run_id", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','blocked','cancelled')",
            name="ck_assistant_runs_status",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["retry_of_run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_runs_workspace_owner", "assistant_runs", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_assistant_runs_conversation_id"), "assistant_runs", ["conversation_id"])
    op.create_index(op.f("ix_assistant_runs_owner_user_id"), "assistant_runs", ["owner_user_id"])
    op.create_index(op.f("ix_assistant_runs_retry_of_run_id"), "assistant_runs", ["retry_of_run_id"])
    op.create_index(op.f("ix_assistant_runs_trace_id"), "assistant_runs", ["trace_id"])
    op.create_index(op.f("ix_assistant_runs_workspace_id"), "assistant_runs", ["workspace_id"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_sequence"),
    )
    op.create_index("ix_run_events_workspace_owner", "run_events", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_run_events_conversation_id"), "run_events", ["conversation_id"])
    op.create_index(op.f("ix_run_events_owner_user_id"), "run_events", ["owner_user_id"])
    op.create_index(op.f("ix_run_events_run_id"), "run_events", ["run_id"])
    op.create_index(op.f("ix_run_events_workspace_id"), "run_events", ["workspace_id"])
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tool_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("policy_findings_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed','blocked')", name="ck_tool_calls_status"),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_calls_run_id"), "tool_calls", ["run_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation_threads.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_workspace_owner", "artifacts", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_artifacts_conversation_id"), "artifacts", ["conversation_id"])
    op.create_index(op.f("ix_artifacts_owner_user_id"), "artifacts", ["owner_user_id"])
    op.create_index(op.f("ix_artifacts_run_id"), "artifacts", ["run_id"])
    op.create_index(op.f("ix_artifacts_workspace_id"), "artifacts", ["workspace_id"])
    op.create_table(
        "strategy_specs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_specs_workspace_owner", "strategy_specs", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_strategy_specs_owner_user_id"), "strategy_specs", ["owner_user_id"])
    op.create_index(op.f("ix_strategy_specs_run_id"), "strategy_specs", ["run_id"])
    op.create_index(op.f("ix_strategy_specs_workspace_id"), "strategy_specs", ["workspace_id"])
    op.create_table(
        "validation_reports",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_reports_workspace_owner", "validation_reports", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_validation_reports_owner_user_id"), "validation_reports", ["owner_user_id"])
    op.create_index(op.f("ix_validation_reports_run_id"), "validation_reports", ["run_id"])
    op.create_index(op.f("ix_validation_reports_workspace_id"), "validation_reports", ["workspace_id"])
    op.create_table(
        "review_reports",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_reports_workspace_owner", "review_reports", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_review_reports_owner_user_id"), "review_reports", ["owner_user_id"])
    op.create_index(op.f("ix_review_reports_run_id"), "review_reports", ["run_id"])
    op.create_index(op.f("ix_review_reports_workspace_id"), "review_reports", ["workspace_id"])
    op.create_table(
        "policy_findings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=80), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policy_findings_workspace_owner", "policy_findings", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_policy_findings_owner_user_id"), "policy_findings", ["owner_user_id"])
    op.create_index(op.f("ix_policy_findings_run_id"), "policy_findings", ["run_id"])
    op.create_index(op.f("ix_policy_findings_tool_call_id"), "policy_findings", ["tool_call_id"])
    op.create_index(op.f("ix_policy_findings_workspace_id"), "policy_findings", ["workspace_id"])
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("tool_id", sa.String(length=120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_estimate_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_ledger_workspace_owner", "usage_ledger", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_usage_ledger_owner_user_id"), "usage_ledger", ["owner_user_id"])
    op.create_index(op.f("ix_usage_ledger_run_id"), "usage_ledger", ["run_id"])
    op.create_index(op.f("ix_usage_ledger_workspace_id"), "usage_ledger", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("usage_ledger")
    op.drop_table("policy_findings")
    op.drop_table("review_reports")
    op.drop_table("validation_reports")
    op.drop_table("strategy_specs")
    op.drop_table("artifacts")
    op.drop_table("tool_calls")
    op.drop_table("run_events")
    op.drop_table("assistant_runs")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_threads")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
