from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RepositoryPort:
    name: str
    method_names: tuple[str, ...]

    def missing_methods(self, repository: Any) -> list[str]:
        return [method_name for method_name in self.method_names if not callable(getattr(repository, method_name, None))]


SERVER_REPOSITORY_PORTS = (
    RepositoryPort(
        name="ConversationStore",
        method_names=(
            "create_conversation",
            "list_conversations",
            "list_conversation_sidebar",
            "get_conversation",
            "update_conversation_title",
            "delete_conversation",
            "create_message",
            "list_messages",
            "list_messages_for_context",
            "get_conversation_memory",
            "upsert_conversation_memory",
            "get_conversation_state_snapshot",
        ),
    ),
    RepositoryPort(
        name="RunStore",
        method_names=(
            "create_run",
            "list_runs",
            "get_run",
            "create_run_job",
            "claim_run_job",
            "complete_run_job",
            "cancel_run_jobs",
            "get_run_job",
            "run_queue_stats",
            "append_run_event",
            "append_run_events",
            "list_run_events",
            "list_run_events_after",
            "summarize_run_events",
            "get_run_progress_snapshot",
            "set_run_status",
            "create_strategy_spec",
            "get_strategy_spec_for_run",
            "get_latest_strategy_spec_for_conversation",
        ),
    ),
    RepositoryPort(
        name="WorkflowTaskStore",
        method_names=(
            "list_workflow_task_continuation_events",
            "upsert_workflow_task",
            "sync_workflow_tasks",
            "list_workflow_tasks",
            "get_workflow_task",
            "submit_workflow_task_response",
            "resolve_workflow_task",
        ),
    ),
    RepositoryPort(
        name="ArtifactStorePort",
        method_names=(
            "create_artifact",
            "create_artifacts",
            "list_artifacts",
            "get_artifact",
            "get_latest_conversation_artifact",
            "list_workspace_artifacts_page",
            "list_conversation_artifacts_page",
        ),
    ),
    RepositoryPort(
        name="BacktestReportStore",
        method_names=(
            "get_backtest_summary",
            "get_backtest_summaries",
            "resolve_backtest_report_run_id",
            "query_backtest_trades",
            "get_backtest_equity_summary",
            "get_backtest_equity_summaries",
        ),
    ),
    RepositoryPort(
        name="AuditUsageStore",
        method_names=(
            "create_validation_report",
            "create_review_report",
            "create_tool_call",
            "complete_tool_call",
            "create_policy_finding",
            "create_usage_ledger",
            "list_tool_calls",
            "list_policy_findings",
            "list_usage_ledger",
            "summarize_account_usage",
        ),
    ),
    RepositoryPort(
        name="NautilusRuntimeStore",
        method_names=(
            "upsert_nautilus_runtime",
            "list_nautilus_runtimes",
            "get_nautilus_runtime",
            "set_nautilus_runtime_state",
            "record_nautilus_runtime_heartbeat",
            "activate_nautilus_runtime_kill_switch",
            "set_nautilus_runtime_desired_state",
            "list_desired_nautilus_runtimes",
            "list_active_nautilus_market_data_subscriptions",
            "claim_nautilus_runtime_lease",
            "renew_nautilus_runtime_lease",
            "release_nautilus_runtime_lease",
            "persist_nautilus_runtime_stream_cursor",
            "append_nautilus_runtime_events_for_worker",
            "append_nautilus_runtime_event",
            "list_nautilus_runtime_events",
            "cleanup_nautilus_heartbeat_events",
        ),
    ),
    RepositoryPort(
        name="BotProposalStore",
        method_names=(
            "create_bot_proposal",
            "get_bot_proposal",
            "mark_bot_proposal_started",
        ),
    ),
    RepositoryPort(
        name="FeedbackStore",
        method_names=("create_feedback",),
    ),
)


def missing_repository_methods(repository: Any, ports: Iterable[RepositoryPort] = SERVER_REPOSITORY_PORTS) -> dict[str, list[str]]:
    missing = {port.name: port.missing_methods(repository) for port in ports}
    return {port_name: method_names for port_name, method_names in missing.items() if method_names}
