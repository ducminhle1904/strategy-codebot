import re
from datetime import UTC
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_engine_for_url
from strategy_codebot.server.database import create_session_factory
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.models import Artifact
from strategy_codebot.server.models import AssistantRun
from strategy_codebot.server.models import BacktestReport
from strategy_codebot.server.models import Base
from strategy_codebot.server.models import ConversationThread
from strategy_codebot.server.models import User
from strategy_codebot.server.models import Workspace
from strategy_codebot.server.schemas import ArtifactResponse
from strategy_codebot.server.repository import InMemoryConversationRepository
from strategy_codebot.server.run_modes import BACKTEST_JOB_MAX_ATTEMPTS
from strategy_codebot.server.sql_repository import SQLAlchemyConversationRepository


AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}

REQUIRED_TABLES = {
    "users",
    "workspaces",
    "workspace_memberships",
    "conversation_threads",
    "conversation_messages",
    "conversation_memories",
    "assistant_runs",
    "run_jobs",
    "run_events",
    "tool_calls",
    "artifacts",
    "backtest_reports",
    "backtest_trade_index",
    "backtest_equity_summary",
    "backtest_runner_stats",
    "strategy_specs",
    "nautilus_runtimes",
    "nautilus_runtime_events",
    "validation_reports",
    "review_reports",
    "policy_findings",
    "usage_ledger",
}

INITIAL_MIGRATION_TABLES = REQUIRED_TABLES - {
    "conversation_memories",
    "run_jobs",
    "backtest_reports",
    "backtest_trade_index",
    "backtest_equity_summary",
    "backtest_runner_stats",
    "nautilus_runtimes",
    "nautilus_runtime_events",
}


def test_sqlalchemy_metadata_includes_api_tables() -> None:
    assert REQUIRED_TABLES.issubset(Base.metadata.tables)


def test_sqlite_schema_can_be_created_from_metadata() -> None:
    engine = create_engine_for_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    assert REQUIRED_TABLES.issubset(set(inspect(engine).get_table_names()))


def test_initial_alembic_migration_contains_core_tables() -> None:
    migration = Path("migrations/versions/0001_api_data_model.py").read_text(encoding="utf-8")

    for table in INITIAL_MIGRATION_TABLES:
        assert f'"{table}"' in migration


def test_conversation_memory_migration_contains_memory_table() -> None:
    migration = Path("migrations/versions/0004_conversation_memory.py").read_text(encoding="utf-8")

    assert '"conversation_memories"' in migration
    assert "covered_message_id" in migration


def test_assistant_run_schema_supports_cancel_and_retry() -> None:
    migration = Path("migrations/versions/0001_api_data_model.py").read_text(encoding="utf-8")

    assert "retry_of_run_id" in Base.metadata.tables["assistant_runs"].c
    assert "retry_of_run_id" not in Base.metadata.tables["conversation_messages"].c
    assert "retry_of_run_id" in migration
    assert "cancelled" in migration


def test_alembic_upgrade_head_creates_api_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "api-schema.sqlite"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine_for_url(f"sqlite+pysqlite:///{database_path}")

    inspector = inspect(engine)
    assert REQUIRED_TABLES.issubset(set(inspector.get_table_names()))
    assert "retry_of_run_id" in [column["name"] for column in inspector.get_columns("assistant_runs")]
    assert "retry_of_run_id" not in [column["name"] for column in inspector.get_columns("conversation_messages")]
    assert "summary" in [column["name"] for column in inspector.get_columns("conversation_memories")]
    runtime_columns = [column["name"] for column in inspector.get_columns("nautilus_runtimes")]
    event_columns = [column["name"] for column in inspector.get_columns("nautilus_runtime_events")]
    assert "heartbeat_count" in runtime_columns
    assert "heartbeat_metrics_json" in runtime_columns
    assert "last_heartbeat_event_at" in runtime_columns
    assert "desired_state" in runtime_columns
    assert "worker_id" in runtime_columns
    assert "lease_until" in runtime_columns
    assert "generation" in runtime_columns
    assert "stream_cursor_json" in runtime_columns
    assert "idempotency_key" in event_columns


def test_opaque_id_prefixes() -> None:
    for prefix in [
        "usr",
        "wsp",
        "conv",
        "msg",
        "run",
        "job",
        "evt",
        "toolcall",
        "art",
        "spec",
        "val",
        "rev",
        "pol",
        "usage",
        "nrt",
        "nevt",
    ]:
        assert re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", opaque_id(prefix))


def test_sqlite_repository_preserves_conversation_api_authorization() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "DB backed"}).json()

    assert re.fullmatch(r"conv_[0-9a-f]{32}", conversation["id"])
    assert client.get("/v1/conversations", headers=AUTH_A).json()["items"] == [conversation]
    assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).json() == conversation
    assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_B).status_code == 404
    assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_OTHER_WORKSPACE).status_code == 404


def test_sqlite_repository_persists_conversation_memory_by_tenant() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    other_auth = AuthContext("user-b", "workspace-a")
    conversation = repository.create_conversation(auth, "Memory test")
    message = repository.create_message(auth, conversation.id, "Remember this context")
    assert message is not None

    memory = repository.upsert_conversation_memory(
        auth,
        conversation.id,
        summary="User is designing an EMA crossover strategy.",
        covered_message_id=message.id,
        estimated_tokens=42,
    )
    assert memory is not None
    assert memory.summary_version == 1
    assert memory.covered_message_id == message.id
    assert repository.get_conversation_memory(auth, conversation.id) == memory
    assert repository.get_conversation_memory(other_auth, conversation.id) is None

    updated = repository.upsert_conversation_memory(
        auth,
        conversation.id,
        summary="Updated strategy memory.",
        covered_message_id=message.id,
        estimated_tokens=12,
    )
    assert updated is not None
    assert updated.id == memory.id
    assert updated.summary_version == 2
    assert updated.summary == "Updated strategy memory."


def test_sqlite_repository_run_job_queue_lifecycle() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Backtest")
    run = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    assert run is not None

    job = repository.create_run_job(
        auth,
        run.id,
        job_type="backtest-preview",
        payload_json={"backtest_config": {"symbol": "BTCUSDT"}},
        max_attempts=2,
    )
    assert job is not None
    assert repository.run_queue_stats(job_type="backtest-preview").queued == 1

    claimed = repository.claim_run_job(job_type="backtest-preview", worker_id="worker-a")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker-a"
    assert repository.run_queue_stats(job_type="backtest-preview").running == 1
    assert repository.run_queue_stats(job_type="backtest-preview").active_running == 1
    assert job.max_attempts == 2

    completed = repository.complete_run_job(claimed.id, status="completed", result_json={"ok": True})
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_json == {"ok": True}
    assert repository.run_queue_stats(job_type="backtest-preview").queued == 0


def test_sqlite_repository_run_job_defaults_to_backtest_max_attempts() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Backtest")
    run = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    assert run is not None

    job = repository.create_run_job(
        auth,
        run.id,
        job_type="backtest-preview",
        payload_json={"backtest_config": {"symbol": "BTCUSDT"}},
    )

    assert job is not None
    assert job.max_attempts == BACKTEST_JOB_MAX_ATTEMPTS


def test_sqlite_repository_run_queue_stats_split_active_and_stale_running_jobs() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Backtest")
    active_run = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    stale_run = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    assert active_run is not None
    assert stale_run is not None
    active_job = repository.create_run_job(
        auth,
        active_run.id,
        job_type="backtest-preview",
        payload_json={"backtest_config": {"symbol": "BTCUSDT"}},
    )
    stale_job = repository.create_run_job(
        auth,
        stale_run.id,
        job_type="backtest-preview",
        payload_json={"backtest_config": {"symbol": "ETHUSDT"}},
    )
    assert active_job is not None
    assert stale_job is not None

    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-active", lease_seconds=300) is not None
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-stale", lease_seconds=-1) is not None

    stats = repository.run_queue_stats(job_type="backtest-preview")
    assert stats.running == 2
    assert stats.active_running == 1
    assert stats.stale_running == 1


def test_sqlite_repository_run_job_claim_respects_workspace_active_limit() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    other_workspace = AuthContext("user-a", "workspace-b")
    conversation = repository.create_conversation(auth, "Backtest")
    other_conversation = repository.create_conversation(other_workspace, "Backtest")
    run_one = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    run_two = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    other_run = repository.create_run(other_workspace, other_conversation.id, status="queued", mode="backtest-preview")
    assert run_one is not None
    assert run_two is not None
    assert other_run is not None
    payload = {"backtest_config": {"symbol": "BTCUSDT"}, "limits": {"workspace_active_limit": 1}}
    first = repository.create_run_job(auth, run_one.id, job_type="backtest-preview", payload_json=payload)
    second = repository.create_run_job(auth, run_two.id, job_type="backtest-preview", payload_json=payload)
    other = repository.create_run_job(other_workspace, other_run.id, job_type="backtest-preview", payload_json=payload)
    assert first is not None
    assert second is not None
    assert other is not None

    claimed = repository.claim_run_job(job_type="backtest-preview", worker_id="worker-a")
    assert claimed is not None
    assert claimed.id == first.id
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-b") is not None
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-c") is None


def test_sqlite_repository_cancel_run_jobs() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    other_auth = AuthContext("user-b", "workspace-a")
    conversation = repository.create_conversation(auth, "Backtest")
    run = repository.create_run(auth, conversation.id, status="queued", mode="backtest-preview")
    assert run is not None
    job = repository.create_run_job(
        auth,
        run.id,
        job_type="backtest-preview",
        payload_json={"backtest_config": {"symbol": "BTCUSDT"}},
    )
    assert job is not None

    assert repository.cancel_run_jobs(other_auth, run.id, result_json={"reason": "api_cancelled"}) == 0
    assert repository.cancel_run_jobs(auth, run.id, result_json={"reason": "api_cancelled"}) == 1

    cancelled = repository.get_run_job(job.id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.result_json == {"reason": "api_cancelled"}
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="worker-a") is None


def test_sqlite_repository_create_artifacts_bulk() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Artifacts")
    run = repository.create_run(auth, conversation.id)
    assert run is not None

    created = repository.create_artifacts(
        auth,
        run.id,
        [
            ("backtest_report", "application/json", "Report", "runs/run_1/report.json", {"source": "test"}),
            ("backtest_trades", "application/json", "Trades", "runs/run_1/trades.json", {"source": "test"}),
        ],
    )

    assert created is not None
    assert [artifact.kind for artifact in created] == ["backtest_report", "backtest_trades"]
    assert [artifact.display_name for artifact in repository.list_artifacts(auth, run.id)] == ["Report", "Trades"]


def test_sqlite_repository_create_artifacts_bulk_rolls_back_on_failure() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Artifacts")
    run = repository.create_run(auth, conversation.id)
    assert run is not None

    try:
        repository.create_artifacts(
            auth,
            run.id,
            [
                ("backtest_report", "application/json", "Report", "runs/run_1/report.json", {}),
                ("backtest_trades", "application/json", "Trades", None, {}),
            ],  # type: ignore[list-item]
        )
    except Exception:
        pass
    else:
        raise AssertionError("bulk artifact insert with invalid storage_key should fail")

    assert repository.list_artifacts(auth, run.id) == []


def test_sqlite_repository_lists_bounded_messages_for_context() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    for index in range(5):
        repository.create_message(auth, conversation.id, f"message {index}")

    messages = repository.list_messages_for_context(auth, conversation.id, limit=3)

    assert [message.content for message in messages] == ["message 2", "message 3", "message 4"]


def test_sqlite_repository_state_snapshot_defaults_to_latest_100_messages() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    for index in range(105):
        repository.create_message(auth, conversation.id, f"message {index:03d}")

    snapshot = repository.get_conversation_state_snapshot(auth, conversation.id)

    assert snapshot is not None
    assert snapshot.message_count == 105
    assert snapshot.message_limit == 100
    assert snapshot.messages_truncated is True
    assert len(snapshot.messages) == 100
    assert snapshot.messages[0].content == "message 005"
    assert snapshot.messages[-1].content == "message 104"


def test_in_memory_repository_state_snapshot_applies_message_limit_metadata() -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    for index in range(3):
        repository.create_message(auth, conversation.id, f"message {index}")

    empty = repository.get_conversation_state_snapshot(auth, conversation.id, message_limit=0)
    oversized = repository.get_conversation_state_snapshot(auth, conversation.id, message_limit=999)

    assert empty is not None
    assert empty.messages == []
    assert empty.message_count == 3
    assert empty.messages_truncated is True
    assert empty.message_limit == 0
    assert oversized is not None
    assert oversized.message_limit == 500
    assert [message.content for message in oversized.messages] == ["message 0", "message 1", "message 2"]


def test_conversation_state_caps_messages_but_messages_endpoint_returns_full_transcript() -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Long thread"}).json()
    for index in range(105):
        response = client.post(
            f"/v1/conversations/{conversation['id']}/messages",
            headers=AUTH_A,
            json={"content": f"message {index:03d}"},
        )
        assert response.status_code == 201

    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).json()
    full_messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).json()

    assert state["message_count"] == 105
    assert state["message_limit"] == 100
    assert state["messages_truncated"] is True
    assert len(state["messages"]) == 100
    assert state["messages"][0]["content"] == "message 005"
    assert len(full_messages["items"]) == 105


def test_sqlite_repository_soft_deletes_conversation() -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Delete me"}).json()

    response = client.delete(f"/v1/conversations/{conversation['id']}", headers=AUTH_A)

    assert response.status_code == 200, response.text
    assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).status_code == 404
    assert repository.list_conversations(AuthContext("user-a", "workspace-a")) == []


def test_sqlite_repository_persists_messages_and_blocks_cross_tenant_writes() -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    message = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "persist me"},
    )
    cross_user = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_B,
        json={"content": "blocked"},
    )
    cross_workspace = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_OTHER_WORKSPACE,
        json={"content": "blocked"},
    )

    assert message.status_code == 201, message.text
    assert cross_user.status_code == 404
    assert cross_workspace.status_code == 404
    messages = repository.list_messages(AuthContext("user-a", "workspace-a"), conversation["id"])
    assert [stored.content for stored in messages] == ["persist me"]


def test_sqlite_repository_preserves_updated_at_ordering() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))

    older = client.post("/v1/conversations", headers=AUTH_A, json={"title": "older"}).json()
    newer = client.post("/v1/conversations", headers=AUTH_A, json={"title": "newer"}).json()
    response = client.post(
        f"/v1/conversations/{older['id']}/messages",
        headers=AUTH_A,
        json={"content": "move older to top"},
    )

    assert response.status_code == 201, response.text
    items = client.get("/v1/conversations", headers=AUTH_A).json()["items"]
    assert [item["id"] for item in items] == [older["id"], newer["id"]]


def test_sqlite_sidebar_uses_latest_message_and_run_summary() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    other_auth = AuthContext("user-b", "workspace-a")
    first = repository.create_conversation(auth, "first")
    second = repository.create_conversation(auth, "second")
    other = repository.create_conversation(other_auth, "other")

    assert repository.create_message(auth, first.id, "first older") is not None
    assert repository.create_message(auth, first.id, "first latest") is not None
    assert repository.create_message(auth, second.id, "second only") is not None
    assert repository.create_message(other_auth, other.id, "other tenant") is not None
    first_run = repository.create_run(auth, first.id, status="queued")
    second_run = repository.create_run(auth, first.id, status="running")
    assert first_run is not None
    assert second_run is not None
    assert repository.set_run_status(auth, first_run.id, "completed") is not None

    sidebar = repository.list_conversation_sidebar(auth)
    first_item = next(item for item in sidebar if item.conversation.id == first.id)
    second_item = next(item for item in sidebar if item.conversation.id == second.id)

    assert {item.conversation.id for item in sidebar} == {first.id, second.id}
    assert first_item.last_message_content == "first latest"
    assert first_item.message_count == 2
    assert first_item.latest_run_id == first_run.id
    assert first_item.latest_run_status == "completed"
    assert second_item.last_message_content == "second only"
    assert second_item.message_count == 1
    assert second_item.latest_run_id is None
    assert second_item.latest_run_status is None


def test_sqlite_repository_summarizes_account_usage_by_tenant() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    other_auth = AuthContext("user-b", "workspace-a")
    conversation = repository.create_conversation(auth)
    repository.create_message(auth, conversation.id, "hello")
    run = repository.create_run(auth, conversation.id)
    assert run is not None
    repository.create_artifact(
        auth,
        run.id,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        storage_key="artifacts/strategy.pine",
    )
    repository.create_usage_ledger(
        auth,
        run_id=run.id,
        model="model-a",
        tool_id=None,
        input_tokens=30,
        output_tokens=12,
        cost_estimate_usd=0.003,
    )
    other_conversation = repository.create_conversation(other_auth)
    repository.create_message(other_auth, other_conversation.id, "hidden")

    usage = repository.summarize_account_usage(auth)
    other_usage = repository.summarize_account_usage(other_auth)

    assert usage.messages == 1
    assert usage.runs == 1
    assert usage.artifacts == 1
    assert usage.input_tokens == 30
    assert usage.output_tokens == 12
    assert usage.total_tokens == 42
    assert usage.estimated_cost_usd == 0.003
    assert other_usage.messages == 1
    assert other_usage.runs == 0
    assert other_usage.artifacts == 0
    assert other_usage.total_tokens == 0


def test_sqlite_repository_bulk_appends_run_events_with_monotonic_sequences() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    run = repository.create_run(auth, conversation.id)
    assert run is not None

    created = repository.append_run_events(
        auth,
        run.id,
        [
            ("tool.started", {"tool_id": "one"}),
            ("tool.completed", {"tool_id": "one"}),
            ("run.completed", {"status": "completed"}),
        ],
    )

    assert created is not None
    assert [event.sequence for event in created] == [1, 2, 3]
    replay = repository.list_run_events(auth, run.id)
    assert replay is not None
    assert [event.type for event in replay] == ["tool.started", "tool.completed", "run.completed"]
    assert [event.sequence for event in repository.list_run_events_after(auth, run.id, "1") or []] == [2, 3]


def test_in_memory_repository_bulk_appends_run_events_like_sql() -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    run = repository.create_run(auth, conversation.id)
    assert run is not None

    created = repository.append_run_events(
        auth,
        run.id,
        [
            ("tool.started", {"tool_id": "one"}),
            ("tool.completed", {"tool_id": "one"}),
        ],
    )

    assert created is not None
    assert [event.sequence for event in created] == [1, 2]
    single = repository.append_run_event(auth, run.id, "run.completed", {"status": "completed"})
    assert single is not None
    assert single.sequence == 3


def test_sqlite_repository_conversation_state_snapshot_uses_latest_run_and_bounded_events() -> None:
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth)
    older_run = repository.create_run(auth, conversation.id, status="running")
    newer_run = repository.create_run(auth, conversation.id, status="running")
    assert older_run is not None
    assert newer_run is not None
    latest_run = repository.set_run_status(auth, older_run.id, "completed")
    assert latest_run is not None
    created = repository.append_run_events(
        auth,
        latest_run.id,
        [(f"debug.progress.{index}", {"index": index}) for index in range(35)],
    )
    assert created is not None
    repository.append_run_event(auth, newer_run.id, "debug.progress.hidden", {"index": 999})

    snapshot = repository.get_conversation_state_snapshot(auth, conversation.id, event_limit=30)
    full_replay = repository.list_run_events(auth, latest_run.id)

    assert snapshot is not None
    assert snapshot.latest_run is not None
    assert snapshot.latest_run.id == latest_run.id
    assert [event.sequence for event in snapshot.latest_run_events] == list(range(6, 36))
    assert [event.payload["index"] for event in snapshot.latest_run_events if event.payload] == list(range(5, 35))
    assert [event.payload["index"] for event in snapshot.conversation_run_events if event.payload] == [
        *range(35),
        999,
    ]
    assert full_replay is not None
    assert len(full_replay) == 35


def test_sqlite_repository_resolves_latest_backtest_report_for_conversation() -> None:
    engine = create_engine_for_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    repository = SQLAlchemyConversationRepository(session_factory)
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Backtest reports")
    older_run = repository.create_run(auth, conversation.id, status="completed", mode="backtest-preview")
    newer_run = repository.create_run(auth, conversation.id, status="completed", mode="backtest-preview")
    other_conversation = repository.create_conversation(auth, "Other backtest reports")
    other_run = repository.create_run(auth, other_conversation.id, status="completed", mode="backtest-preview")
    assert older_run is not None
    assert newer_run is not None
    assert other_run is not None

    with session_factory() as session:
        session.add_all(
            [
                _backtest_report("report_old", older_run.id, created_at=datetime(2026, 1, 1, tzinfo=UTC)),
                _backtest_report("report_new", newer_run.id, created_at=datetime(2026, 1, 2, tzinfo=UTC)),
                _backtest_report("report_other", other_run.id, created_at=datetime(2026, 1, 3, tzinfo=UTC)),
            ]
        )
        session.commit()

    assert repository.resolve_backtest_report_run_id(auth, conversation.id, older_run.id) == older_run.id
    assert repository.resolve_backtest_report_run_id(auth, conversation.id, "run_hallucinated") == newer_run.id
    assert (
        repository.resolve_backtest_report_run_id(AuthContext("user-b", "workspace-a"), conversation.id, "run_hallucinated")
        is None
    )


def test_artifact_storage_key_is_internal_to_public_serializer() -> None:
    engine = create_engine_for_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        artifact = _insert_artifact(session)
        public = ArtifactResponse.model_validate(artifact).model_dump()

    assert artifact.storage_key == "private/runs/run_a/strategy.pine"
    assert public["id"] == artifact.id
    assert public["display_name"] == "strategy.pine"
    assert "storage_key" not in public


def _backtest_report(report_id: str, run_id: str, *, created_at: datetime) -> BacktestReport:
    return BacktestReport(
        id=report_id,
        run_id=run_id,
        owner_user_id="user-a",
        workspace_id="workspace-a",
        engine="pineforge",
        evidence_label="Local preview evidence",
        execution_semantics="model_generated_pine_pineforge",
        symbol="BTC/USDT",
        signal_timeframe="1h",
        candle_timeframe="1m",
        metrics_json={"trade_count": 1},
        assumptions_json=[],
        warnings_json=[],
        reproducibility_hash=None,
        created_at=created_at,
    )


def _insert_artifact(session: Session) -> Artifact:
    user = User(id="user-a", external_subject="user-a", display_name="user-a")
    workspace = Workspace(id="workspace-a", name="workspace-a")
    conversation = ConversationThread(
        id="conv_a",
        owner_user_id=user.id,
        workspace_id=workspace.id,
        title="Artifact test",
        mode="strategy_design",
    )
    run = AssistantRun(
        id="run_a",
        conversation_id=conversation.id,
        owner_user_id=user.id,
        workspace_id=workspace.id,
        status="completed",
    )
    artifact = Artifact(
        id="art_a",
        run_id=run.id,
        conversation_id=conversation.id,
        owner_user_id=user.id,
        workspace_id=workspace.id,
        kind="pine",
        mime_type="text/plain",
        display_name="strategy.pine",
        storage_key="private/runs/run_a/strategy.pine",
        metadata_json={"source": "test"},
    )
    session.add_all([user, workspace, conversation, run, artifact])
    session.commit()
    return artifact
