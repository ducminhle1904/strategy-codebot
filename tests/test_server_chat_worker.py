from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.chat_worker import run_chat_worker
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.models import BacktestReport
from strategy_codebot.server.models import RunJob
from strategy_codebot.server.run_modes import CHAT_BACKTEST_SUMMARY_JOB_TYPE
from strategy_codebot.server.run_modes import PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE
from strategy_codebot.server.run_modes import RUN_MODE_BACKTEST_PREVIEW


VALID_REPAIRED_PINE = """//@version=6
strategy("Preview compatible")
strategy.entry("Long", strategy.long)
strategy.exit("Exit", "Long", stop=close * 0.98, limit=close * 1.04)
"""


class FakeRepairClient:
    model = "fake-repair"

    def __init__(self, content: str) -> None:
        self.content = content
        self.routing_contexts = []
        self.messages = []

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages, tools, routing_context=None):
        self.routing_contexts.append(routing_context or {})
        self.messages.append(messages)
        yield LLMClientEvent(type=LLM_EVENT_MESSAGE_DELTA, text=self.content, model=self.model)


def test_chat_worker_appends_backtest_summary_message_once() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Auto chain")
    run = repository.create_run(auth, conversation.id, status="completed", mode="backtest-preview")
    assert run is not None
    _insert_backtest_report(repository, auth, run.id)
    payload = {
        "backtest_run_id": run.id,
        "conversation_id": conversation.id,
        "source_run_id": "run_parent",
        "summary_on_complete": True,
    }
    assert repository.create_run_job(auth, run.id, job_type=CHAT_BACKTEST_SUMMARY_JOB_TYPE, payload_json=payload) is not None

    assert run_chat_worker(repository, worker_id="test-chat-worker", once=True) == 1
    assert repository.create_run_job(auth, run.id, job_type=CHAT_BACKTEST_SUMMARY_JOB_TYPE, payload_json=payload) is not None
    assert run_chat_worker(repository, worker_id="test-chat-worker", once=True) == 1

    messages = repository.list_messages(auth, conversation.id)
    assistant_messages = [message for message in messages if message.role == "assistant"]
    assert len(assistant_messages) == 1
    assert "Backtest completed for ETH/USDT" in assistant_messages[0].content
    assert "-894.3091" in assistant_messages[0].content
    events = repository.list_run_events(auth, run.id)
    assert events is not None
    assert any(event.type == "chat.auto_chain.summary.completed" for event in events)


def test_chat_worker_failure_event_is_mirrored_to_source_run() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Auto chain")
    source_run = repository.create_run(auth, conversation.id, status="completed", mode="agent")
    backtest_run = repository.create_run(auth, conversation.id, status="completed", mode="backtest-preview")
    assert source_run is not None
    assert backtest_run is not None
    payload = {
        "backtest_run_id": backtest_run.id,
        "conversation_id": conversation.id,
        "source_run_id": source_run.id,
        "summary_on_complete": True,
    }
    assert (
        repository.create_run_job(auth, backtest_run.id, job_type=CHAT_BACKTEST_SUMMARY_JOB_TYPE, payload_json=payload)
        is not None
    )

    assert run_chat_worker(repository, worker_id="test-chat-worker", once=True) == 0

    source_events = repository.list_run_events(auth, source_run.id)
    backtest_events = repository.list_run_events(auth, backtest_run.id)
    assert source_events is not None
    assert backtest_events is not None
    assert any(event.type == "chat.auto_chain.failed" for event in source_events)
    assert any(event.type == "chat.auto_chain.failed" for event in backtest_events)


def test_chat_worker_repairs_preview_compatibility_and_queues_repaired_run() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Auto chain")
    source_run = repository.create_run(auth, conversation.id, status="running", mode="agent")
    failed_run = repository.create_run(auth, conversation.id, status="failed", mode=RUN_MODE_BACKTEST_PREVIEW)
    assert source_run is not None
    assert failed_run is not None
    payload = {
        "failed_backtest_run_id": failed_run.id,
        "failed_job_id": "job_failed",
        "source_run_id": source_run.id,
        "conversation_id": conversation.id,
        "preview_error_code": "preview_compatibility_limit",
        "strategy_spec": _strategy_spec(),
        "pine_code": '//@version=6\nstrategy("Uses unsupported")\nhtf = request.security(syminfo.tickerid, "D", close)\nstrategy.entry("Long", strategy.long)\n',
        "backtest_config": _backtest_config(),
        "auto_chain": {"summary_on_complete": True, "source_run_id": source_run.id, "conversation_id": conversation.id},
        "compatibility_repair": {"attempt": 1, "max_attempts": 2},
        "internal_diagnostics": {"raw_runtime_message": "internal compile details"},
    }
    assert (
        repository.create_run_job(auth, failed_run.id, job_type=PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE, payload_json=payload)
        is not None
    )
    client = FakeRepairClient(
        '{"pine_code": '
        + __import__("json").dumps(VALID_REPAIRED_PINE)
        + ', "repair_notes": "Replaced unsupported preview construct.", "unsupported_surface_summary": "higher timeframe helper"}'
    )

    assert run_chat_worker(repository, worker_id="test-chat-worker", repair_client=client, once=True) == 1

    runs = repository.list_runs(auth, conversation.id)
    assert runs is not None
    repaired_runs = [run for run in runs if run.retry_of_run_id == failed_run.id]
    assert len(repaired_runs) == 1
    assert repaired_runs[0].status == "queued"
    assert client.routing_contexts[0]["stage"] == "repair"
    prompt_text = " ".join(message["content"] for message in client.messages[0])
    assert "Do not mention unsupported API or function names inside generated Pine comments." in prompt_text

    source_events = repository.list_run_events(auth, source_run.id)
    failed_events = repository.list_run_events(auth, failed_run.id)
    repaired_events = repository.list_run_events(auth, repaired_runs[0].id)
    assert source_events is not None
    assert failed_events is not None
    assert repaired_events is not None
    assert any(event.type == "validation.repair.started" for event in failed_events)
    completed_events = [event for event in source_events if event.type == "validation.repair.completed"]
    assert completed_events
    assert completed_events[-1].payload["message"] == "Compatibility repair applied. Re-running local preview."
    repaired_payload = _preview_job_payload(repository, repaired_runs[0].id)
    assert repaired_payload["auto_chain"]["summary_on_complete"] is True
    visible_text = " ".join(str(event.payload) for event in source_events + failed_events + repaired_events)
    assert "pineforge" not in visible_text.lower()
    assert "runner" not in visible_text.lower()


def test_chat_worker_repair_preserves_summary_auto_chain_opt_out() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Auto chain")
    failed_run = repository.create_run(auth, conversation.id, status="failed", mode=RUN_MODE_BACKTEST_PREVIEW)
    assert failed_run is not None
    payload = {
        "failed_backtest_run_id": failed_run.id,
        "conversation_id": conversation.id,
        "preview_error_code": "preview_compatibility_limit",
        "strategy_spec": _strategy_spec(),
        "pine_code": VALID_REPAIRED_PINE,
        "backtest_config": _backtest_config(),
        "auto_chain": {"summary_on_complete": False, "conversation_id": conversation.id},
        "compatibility_repair": {"attempt": 1, "max_attempts": 2},
    }
    assert (
        repository.create_run_job(auth, failed_run.id, job_type=PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE, payload_json=payload)
        is not None
    )
    client = FakeRepairClient('{"pine_code": ' + __import__("json").dumps(VALID_REPAIRED_PINE) + "}")

    assert run_chat_worker(repository, worker_id="test-chat-worker", repair_client=client, once=True) == 1

    runs = repository.list_runs(auth, conversation.id)
    assert runs is not None
    repaired_runs = [run for run in runs if run.retry_of_run_id == failed_run.id]
    assert len(repaired_runs) == 1
    repaired_payload = _preview_job_payload(repository, repaired_runs[0].id)
    assert repaired_payload["auto_chain"]["summary_on_complete"] is False


def test_chat_worker_repair_failure_uses_manual_validation_copy() -> None:
    auth = AuthContext("user-a", "workspace-a")
    repository = create_sqlite_repository()
    conversation = repository.create_conversation(auth, title="Auto chain")
    failed_run = repository.create_run(auth, conversation.id, status="failed", mode=RUN_MODE_BACKTEST_PREVIEW)
    assert failed_run is not None
    payload = {
        "failed_backtest_run_id": failed_run.id,
        "conversation_id": conversation.id,
        "preview_error_code": "preview_compatibility_limit",
        "strategy_spec": _strategy_spec(),
        "pine_code": VALID_REPAIRED_PINE,
        "backtest_config": _backtest_config(),
        "compatibility_repair": {"attempt": 2, "max_attempts": 2},
    }
    assert (
        repository.create_run_job(auth, failed_run.id, job_type=PREVIEW_COMPATIBILITY_REPAIR_JOB_TYPE, payload_json=payload)
        is not None
    )

    assert run_chat_worker(repository, worker_id="test-chat-worker", repair_client=FakeRepairClient("{}"), once=True) == 1

    events = repository.list_run_events(auth, failed_run.id)
    assert events is not None
    failed_events = [event for event in events if event.type == "validation.repair.failed"]
    assert failed_events
    assert failed_events[-1].payload["manual_validation_required"] is True
    assert (
        failed_events[-1].payload["message"]
        == "Local preview cannot run part of this script yet. The Pine code may still require manual platform validation."
    )


def test_chat_worker_env_parsing_falls_back_to_positive_defaults(monkeypatch) -> None:
    from strategy_codebot.server.chat_worker import _positive_float_env, _positive_int_env

    monkeypatch.setenv("STRATEGY_CODEBOT_CHAT_WORKER_LEASE_SECONDS", "0")
    monkeypatch.setenv("STRATEGY_CODEBOT_CHAT_WORKER_POLL_INTERVAL_SECONDS", "not-a-number")

    assert _positive_int_env("STRATEGY_CODEBOT_CHAT_WORKER_LEASE_SECONDS", 60) == 60
    assert _positive_float_env("STRATEGY_CODEBOT_CHAT_WORKER_POLL_INTERVAL_SECONDS", 2.0) == 2.0


def _insert_backtest_report(repository, auth: AuthContext, run_id: str) -> None:
    with repository._session_factory() as session:  # noqa: SLF001
        session.add(
            BacktestReport(
                id="btr_test",
                run_id=run_id,
                owner_user_id=auth.user_id,
                workspace_id=auth.workspace_id,
                engine="pineforge",
                evidence_label="PineForge local Pine preview evidence",
                execution_semantics="model_generated_pine_pineforge",
                symbol="ETH/USDT",
                signal_timeframe="1h",
                candle_timeframe="1m",
                metrics_json={
                    "pnl": {"absolute": -894.3091, "percentage": -8.9431},
                    "max_drawdown": None,
                    "trade_count": 87,
                    "win_rate": 0,
                },
                assumptions_json=[],
                warnings_json=[],
                reproducibility_hash="hash_123",
            )
        )
        session.commit()


def _preview_job_payload(repository, run_id: str) -> dict:
    with repository._session_factory() as session:  # noqa: SLF001
        job = session.query(RunJob).filter_by(run_id=run_id, job_type=RUN_MODE_BACKTEST_PREVIEW).one()
        return job.payload_json


def _strategy_spec() -> dict:
    return {
        "name": "Preview compatible",
        "script_type": "strategy",
        "position_sizing": "risk 1% per trade",
        "stop_loss": "2%",
        "take_profit": "4%",
        "risk_rules": ["Use explicit stop and take profit."],
    }


def _backtest_config() -> dict:
    return {
        "engine": "pineforge",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candle_timeframe": "1m",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }
