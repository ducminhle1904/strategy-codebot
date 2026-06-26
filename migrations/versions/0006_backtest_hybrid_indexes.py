"""backtest hybrid query indexes

Revision ID: 0006_backtest_hybrid_indexes
Revises: 0005_run_jobs
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_backtest_hybrid_indexes"
down_revision: str | None = "0005_run_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artifacts_workspace_owner_created_id",
        "artifacts",
        ["workspace_id", "owner_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_artifacts_conversation_workspace_owner_created_id",
        "artifacts",
        ["conversation_id", "workspace_id", "owner_user_id", "created_at", "id"],
    )

    op.create_table(
        "backtest_reports",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=40), nullable=False),
        sa.Column("evidence_label", sa.String(length=160), nullable=False),
        sa.Column("execution_semantics", sa.String(length=80), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("signal_timeframe", sa.String(length=16), nullable=False),
        sa.Column("candle_timeframe", sa.String(length=16), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("reproducibility_hash", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_backtest_reports_run"),
    )
    op.create_index("ix_backtest_reports_workspace_owner", "backtest_reports", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_backtest_reports_owner_user_id"), "backtest_reports", ["owner_user_id"])
    op.create_index(op.f("ix_backtest_reports_reproducibility_hash"), "backtest_reports", ["reproducibility_hash"])
    op.create_index(op.f("ix_backtest_reports_run_id"), "backtest_reports", ["run_id"])
    op.create_index(op.f("ix_backtest_reports_workspace_id"), "backtest_reports", ["workspace_id"])

    op.create_table(
        "backtest_trade_index",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("trade_rank", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(length=40), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pnl_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("pnl_percentage", sa.Numeric(18, 8), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "trade_rank", name="uq_backtest_trade_index_run_rank"),
    )
    op.create_index("ix_backtest_trade_index_run_bucket", "backtest_trade_index", ["run_id", "bucket"])
    op.create_index("ix_backtest_trade_index_workspace_owner", "backtest_trade_index", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_backtest_trade_index_owner_user_id"), "backtest_trade_index", ["owner_user_id"])
    op.create_index(op.f("ix_backtest_trade_index_run_id"), "backtest_trade_index", ["run_id"])
    op.create_index(op.f("ix_backtest_trade_index_workspace_id"), "backtest_trade_index", ["workspace_id"])

    op.create_table(
        "backtest_equity_summary",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("sample_resolution", sa.String(length=40), nullable=False),
        sa.Column("points_json", sa.JSON(), nullable=False),
        sa.Column("drawdown_windows_json", sa.JSON(), nullable=True),
        sa.Column("monthly_returns_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_backtest_equity_summary_run"),
    )
    op.create_index("ix_backtest_equity_summary_workspace_owner", "backtest_equity_summary", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_backtest_equity_summary_owner_user_id"), "backtest_equity_summary", ["owner_user_id"])
    op.create_index(op.f("ix_backtest_equity_summary_run_id"), "backtest_equity_summary", ["run_id"])
    op.create_index(op.f("ix_backtest_equity_summary_workspace_id"), "backtest_equity_summary", ["workspace_id"])

    op.create_table(
        "backtest_runner_stats",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("runner", sa.String(length=80), nullable=False),
        sa.Column("runner_version", sa.String(length=120), nullable=True),
        sa.Column("bars_processed", sa.Integer(), nullable=False),
        sa.Column("compile_ms", sa.Integer(), nullable=False),
        sa.Column("run_ms", sa.Integer(), nullable=False),
        sa.Column("output_bytes", sa.Integer(), nullable=False),
        sa.Column("artifact_manifest_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_backtest_runner_stats_run"),
    )
    op.create_index("ix_backtest_runner_stats_workspace_owner", "backtest_runner_stats", ["workspace_id", "owner_user_id"])
    op.create_index(op.f("ix_backtest_runner_stats_owner_user_id"), "backtest_runner_stats", ["owner_user_id"])
    op.create_index(op.f("ix_backtest_runner_stats_run_id"), "backtest_runner_stats", ["run_id"])
    op.create_index(op.f("ix_backtest_runner_stats_workspace_id"), "backtest_runner_stats", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_conversation_workspace_owner_created_id", table_name="artifacts")
    op.drop_index("ix_artifacts_workspace_owner_created_id", table_name="artifacts")
    op.drop_index(op.f("ix_backtest_runner_stats_workspace_id"), table_name="backtest_runner_stats")
    op.drop_index(op.f("ix_backtest_runner_stats_run_id"), table_name="backtest_runner_stats")
    op.drop_index(op.f("ix_backtest_runner_stats_owner_user_id"), table_name="backtest_runner_stats")
    op.drop_index("ix_backtest_runner_stats_workspace_owner", table_name="backtest_runner_stats")
    op.drop_table("backtest_runner_stats")
    op.drop_index(op.f("ix_backtest_equity_summary_workspace_id"), table_name="backtest_equity_summary")
    op.drop_index(op.f("ix_backtest_equity_summary_run_id"), table_name="backtest_equity_summary")
    op.drop_index(op.f("ix_backtest_equity_summary_owner_user_id"), table_name="backtest_equity_summary")
    op.drop_index("ix_backtest_equity_summary_workspace_owner", table_name="backtest_equity_summary")
    op.drop_table("backtest_equity_summary")
    op.drop_index(op.f("ix_backtest_trade_index_workspace_id"), table_name="backtest_trade_index")
    op.drop_index(op.f("ix_backtest_trade_index_run_id"), table_name="backtest_trade_index")
    op.drop_index(op.f("ix_backtest_trade_index_owner_user_id"), table_name="backtest_trade_index")
    op.drop_index("ix_backtest_trade_index_workspace_owner", table_name="backtest_trade_index")
    op.drop_index("ix_backtest_trade_index_run_bucket", table_name="backtest_trade_index")
    op.drop_table("backtest_trade_index")
    op.drop_index(op.f("ix_backtest_reports_workspace_id"), table_name="backtest_reports")
    op.drop_index(op.f("ix_backtest_reports_run_id"), table_name="backtest_reports")
    op.drop_index(op.f("ix_backtest_reports_reproducibility_hash"), table_name="backtest_reports")
    op.drop_index(op.f("ix_backtest_reports_owner_user_id"), table_name="backtest_reports")
    op.drop_index("ix_backtest_reports_workspace_owner", table_name="backtest_reports")
    op.drop_table("backtest_reports")
