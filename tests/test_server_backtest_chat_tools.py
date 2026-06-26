from datetime import UTC
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from strategy_codebot.pine import validate_pineforge_pine
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.app import create_app
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import decide_backtest_preview_approval
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import provider_tools
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import InMemoryConversationRepository
from tests.server_helpers import valid_spec


PINEFORGE_STRATEGY = """//@version=6
strategy("POC EMA RSI", overlay=true)
fast = ta.ema(close, 12)
slow = ta.ema(close, 26)
rsi = ta.rsi(close, 14)
if ta.crossover(fast, slow) and rsi < 70
    strategy.entry("Long", strategy.long)
strategy.exit("Long exit", "Long", stop=close * 0.98, limit=close * 1.04)
"""


def _auth() -> AuthContext:
    return AuthContext(user_id="user-a", workspace_id="workspace-a", user_tier="free")


AUTH_HEADERS = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a", "X-User-Tier": "free"}


def _tool_context(tmp_path: Path) -> tuple[ToolExecutionContext, InMemoryConversationRepository]:
    repository = InMemoryConversationRepository()
    conversation = repository.create_conversation(_auth(), title="Backtest chat")
    run = repository.create_run(_auth(), conversation.id, status="running", mode="agent")
    assert run is not None
    return (
        ToolExecutionContext(
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            auth=_auth(),
            run=run,
        ),
        repository,
    )


class BacktestResultRepository(InMemoryConversationRepository):
    def __init__(self, resolved_run_id: str, conversation_id: str) -> None:
        super().__init__()
        self._resolved_run_id = resolved_run_id
        self._conversation_id = conversation_id

    def resolve_backtest_report_run_id(
        self,
        auth: AuthContext,
        conversation_id: str,
        requested_run_id: str,
    ) -> str | None:
        if conversation_id != self._conversation_id:
            return None
        if requested_run_id == self._resolved_run_id:
            return requested_run_id
        return self._resolved_run_id

    def get_backtest_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        if run_id != self._resolved_run_id:
            return None
        return {"run_id": run_id, "symbol": "BTC/USDT", "metrics": {"trade_count": 2}}

    def query_backtest_trades(
        self,
        auth: AuthContext,
        run_id: str,
        *,
        bucket: str | None = None,
        limit: int = 20,
    ) -> list[dict] | None:
        if run_id != self._resolved_run_id:
            return None
        return [{"bucket": bucket or "sample", "trade_rank": 0, "trade": {"side": "long"}}]

    def get_backtest_equity_summary(self, auth: AuthContext, run_id: str) -> dict | None:
        if run_id != self._resolved_run_id:
            return None
        return {
            "run_id": run_id,
            "points": [{"time": "2024-01-01T00:00:00Z", "equity": 10000}],
        }


def _result_tool_context(tmp_path: Path) -> tuple[ToolExecutionContext, str]:
    repository = BacktestResultRepository("run_completed_backtest", "conv_backtest")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    active_run = AssistantRunRecord(
        id="run_active_chat",
        conversation_id="conv_backtest",
        owner_user_id=_auth().user_id,
        workspace_id=_auth().workspace_id,
        status="running",
        created_at=now,
        updated_at=now,
        mode="agent",
        retry_of_run_id=None,
        request_id="req_test",
        trace_id="trace_test",
    )
    return (
        ToolExecutionContext(
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            auth=_auth(),
            run=active_run,
        ),
        "run_completed_backtest",
    )


def test_provider_tools_include_backtest_chat_actions() -> None:
    tool_names = {tool["name"] for tool in provider_tools()}

    assert {
        "create_backtest_plan",
        "run_backtest_preview",
        "run_backtest_variant_lab",
        "get_backtest_summary",
        "query_backtest_trades",
        "build_robustness_report",
        "get_equity_curve_sample",
    } <= tool_names


def test_backtest_result_tools_fallback_to_latest_conversation_report(tmp_path: Path) -> None:
    context, resolved_run_id = _result_tool_context(tmp_path)

    summary = execute_tool("get_backtest_summary", {"run_id": "run_hallucinated"}, context)
    trades = execute_tool("query_backtest_trades", {"run_id": "run_hallucinated", "bucket": "sample"}, context)
    equity = execute_tool("get_equity_curve_sample", {"run_id": "run_hallucinated"}, context)

    assert summary["status"] == "ok"
    assert summary["run_id"] == resolved_run_id
    assert summary["requested_run_id"] == "run_hallucinated"
    assert summary["fallback_used"] is True
    assert summary["summary"]["metrics"]["trade_count"] == 2
    assert trades["status"] == "ok"
    assert trades["run_id"] == resolved_run_id
    assert trades["trades"][0]["trade"]["side"] == "long"
    assert equity["status"] == "ok"
    assert equity["run_id"] == resolved_run_id
    assert equity["equity_summary"]["points"][0]["equity"] == 10000


def test_build_robustness_report_persists_review_artifact(tmp_path: Path) -> None:
    repository = BacktestResultRepository("run_completed_backtest", "")
    conversation = repository.create_conversation(_auth(), title="Backtest chat")
    repository._conversation_id = conversation.id
    run = repository.create_run(_auth(), conversation.id, status="running", mode="agent")
    assert run is not None
    context = ToolExecutionContext(
        repository=repository,
        artifact_store=LocalArtifactStore(tmp_path),
        auth=_auth(),
        run=run,
    )

    output = execute_tool("build_robustness_report", {"run_id": "run_hallucinated"}, context)

    assert output["status"] == "ok"
    assert output["run_id"] == "run_completed_backtest"
    assert output["fallback_used"] is True
    assert output["artifact_id"]
    report = output["robustness_report"]
    assert report["kind"] == "robustness_report"
    assert report["recommendation"] in {"needs_more_evidence", "candidate_for_review", "reject_preview"}
    assert "not TradingView proof" in report["boundary"]
    artifacts = repository.list_artifacts(_auth(), run.id)
    assert artifacts is not None
    assert artifacts[0].kind == "robustness_report"


def test_create_backtest_plan_normalizes_prompt_into_pineforge_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest ETHUSDT 30m from 2024-01-01 to 2024-03-01 with capital 25000",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
        },
        context,
    )

    assert output["backtest_config"] == {
        "exchange": "binance",
        "symbol": "ETH/USDT",
        "timeframe": "30m",
        "candle_timeframe": "1m",
        "start": "2024-01-01",
        "end": "2024-03-01",
        "initial_capital": 25000.0,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
    }
    assert output["pine_code"].startswith("//@version=6")
    assert output["requires_user_approval"] is True
    assert output["approval_status"] == "pending"
    assert isinstance(output["approval_id"], str)
    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_plan"
    payload = context.artifact_store.read_content(artifact)
    assert payload["approval_id"] == output["approval_id"]
    assert payload["requires_user_approval"] is True
    assert "engine" not in payload["backtest_config"]
    assert "execution_semantics" not in payload
    events = repository.list_run_events(_auth(), context.run.id)
    assert events is not None
    assert any(
        event.type == "backtest.preview.approval_required"
        and event.payload["approval_id"] == output["approval_id"]
        for event in events
    )
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker") is None


def test_create_backtest_plan_rejects_unsupported_executable_timeframe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, _repository = _tool_context(tmp_path)

    try:
        execute_tool(
            "create_backtest_plan",
            {
                "prompt": "Backtest ETHUSDT 4h from 2024-01-01 to 2024-03-01 with capital 25000",
                "strategy_spec": valid_spec(),
                "pine_code": PINEFORGE_STRATEGY,
            },
            context,
        )
    except Exception as exc:
        assert "timeframe" in str(exc)
    else:
        raise AssertionError("unsupported executable timeframe should fail validation")


def test_create_backtest_plan_extracts_requested_exchange(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, _repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest BTCUSDT 1h on OKX from 2024-01-01 to 2024-02-01",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
        },
        context,
    )

    assert output["backtest_config"]["exchange"] == "okx"


def test_create_backtest_plan_rejects_unsupported_exchange(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, _repository = _tool_context(tmp_path)

    try:
        execute_tool(
            "create_backtest_plan",
            {
                "prompt": "Backtest BTCUSDT 1h",
                "strategy_spec": valid_spec(),
                "pine_code": PINEFORGE_STRATEGY,
                "backtest_config": {"exchange": "coinbase"},
            },
            context,
        )
    except Exception as exc:
        assert "exchange" in str(exc)
    else:
        raise AssertionError("unsupported exchange should fail validation")


def test_run_backtest_preview_tool_requires_user_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    config = {
        "engine": "pineforge",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }

    try:
        execute_tool(
            "run_backtest_preview",
            {
                "approval_id": "approval_missing",
                "strategy_spec": valid_spec(),
                "pine_code": PINEFORGE_STRATEGY,
                "backtest_config": config,
                "prompt": "Run preview",
            },
            context,
        )
    except Exception as exc:
        assert "approves" in str(exc) or "approval" in str(exc)
    else:
        raise AssertionError("run_backtest_preview should require an approved plan")

    assert repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker") is None


def test_backtest_approval_queues_child_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    plan = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest BTCUSDT 1h from 2024-01-01 to 2024-02-01 with capital 10000",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
        },
        context,
    )

    output = decide_backtest_preview_approval(
        repository,
        context.artifact_store,
        _auth(),
        conversation_id=context.run.conversation_id,
        approval_id=plan["approval_id"],
        decision="approved",
    )

    child_run = repository.get_run(_auth(), output["run_id"])
    assert child_run is not None
    assert child_run.status == "queued"
    assert child_run.mode == "backtest-preview"
    job = repository.get_run_job(output["job_id"])
    assert job is not None
    assert job.payload_json["backtest_config"]["symbol"] == "BTC/USDT"
    assert job.payload_json["backtest_config"]["exchange"] == "binance"
    assert job.payload_json["backtest_config"]["candle_timeframe"] == "1m"
    assert "strategy_" + "logic" not in job.payload_json
    assert job.payload_json["pine_code"].startswith("//@version=6")
    assert job.payload_json["runtime"]["allowed_api"] == ["pineforge-runner", "pineforge-engine-native"]
    assert "execution_semantics" not in output
    source_events = repository.list_run_events(_auth(), context.run.id)
    assert source_events is not None
    assert any(event.type == "backtest.preview.approved" for event in source_events)
    assert any(event.type == "backtest.preview.queued" for event in source_events)
    assert any(event.type == "chat.auto_chain.waiting_for_backtest" for event in source_events)


def test_backtest_approval_rejection_does_not_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    plan = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest BTCUSDT 1h from 2024-01-01 to 2024-02-01 with capital 10000",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
        },
        context,
    )

    output = decide_backtest_preview_approval(
        repository,
        context.artifact_store,
        _auth(),
        conversation_id=context.run.conversation_id,
        approval_id=plan["approval_id"],
        decision="rejected",
    )

    assert output["status"] == "rejected"
    assert repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker") is None
    source_events = repository.list_run_events(_auth(), context.run.id)
    assert source_events is not None
    assert any(event.type == "backtest.preview.rejected" for event in source_events)


def test_backtest_approval_endpoint_queues_child_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    plan = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest BTCUSDT 1h from 2024-01-01 to 2024-02-01 with capital 10000",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
        },
        context,
    )
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))

    response = client.post(
        f"/v1/conversations/{context.run.conversation_id}/backtest-approvals/{plan['approval_id']}",
        headers=AUTH_HEADERS,
        json={"decision": "approved"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["approval_id"] == plan["approval_id"]
    job = repository.claim_run_job(job_type="backtest-preview", worker_id="test-worker")
    assert job is not None
    assert job.run_id == payload["run_id"]


def test_pineforge_guardrail_blocks_alerts() -> None:
    report = validate_pineforge_pine(
        PINEFORGE_STRATEGY + "\nalert('live signal')\n",
        valid_spec(),
    )

    assert report["status"] == "fail"
    assert any(check["name"] == "pineforge_blocked_constructs" and check["status"] == "fail" for check in report["checks"])


def test_pineforge_guardrail_blocks_executable_request_security() -> None:
    report = validate_pineforge_pine(
        PINEFORGE_STRATEGY + '\nhtf = request.security(syminfo.tickerid, "D", close)\n',
        valid_spec(),
    )

    assert report["status"] == "fail"
    assert any(check["name"] == "pineforge_blocked_constructs" and check["status"] == "fail" for check in report["checks"])


def test_pineforge_guardrail_ignores_blocked_construct_names_in_comments() -> None:
    report = validate_pineforge_pine(
        PINEFORGE_STRATEGY
        + '\n// Local-preview replacement for request.security(..., "D", close).\n'
        + "\n/* request.seed and alertcondition are documented here but not executed. */\n",
        valid_spec(),
    )

    assert not any(check["name"] == "pineforge_blocked_constructs" and check["status"] == "fail" for check in report["checks"])


def test_pineforge_guardrail_accepts_live_repair_diagnostic_sample() -> None:
    repaired = """//@version=6
strategy("HTF RSI pullback", overlay=true)

// Local-preview replacement for request.security(..., "D", close).
// Assumes the strategy is run on the intended 1h signal timeframe.
htfCloseProxy = close[24]
rsi = ta.rsi(close, 14)
longCondition = not na(htfCloseProxy) and close > htfCloseProxy and rsi < 45
riskPct = 0.01
stopPct = 0.02
takePct = 0.04
stopPriceForSizing = close * (1 - stopPct)
riskPerUnit = close - stopPriceForSizing
qty = riskPerUnit > 0 ? (strategy.equity * riskPct) / riskPerUnit : na
if longCondition and strategy.position_size <= 0 and not na(qty)
    strategy.entry("Long", strategy.long, qty=qty)
if strategy.position_size > 0
    strategy.exit("Risk", "Long", stop=strategy.position_avg_price * (1 - stopPct), limit=strategy.position_avg_price * (1 + takePct))
"""
    report = validate_pineforge_pine(repaired, valid_spec())

    assert report["status"] in {"pass", "manual_required"}
    assert not any(check["name"] == "pineforge_blocked_constructs" and check["status"] == "fail" for check in report["checks"])


def test_pineforge_guardrail_blocks_cash_sizing_encoded_as_fixed_qty() -> None:
    spec = {
        **valid_spec(),
        "position_sizing": "Fixed $1,000",
        "risk_rules": ["Fixed position size of $1,000 per trade"],
    }
    report = validate_pineforge_pine(
        """//@version=6
strategy("RSI Reversal", overlay=true, default_qty_type=strategy.fixed, default_qty_value=1000)
if ta.crossover(close, ta.sma(close, 20))
    strategy.entry("Long", strategy.long)
strategy.exit("Exit", "Long", stop=close * 0.98, limit=close * 1.04)
""",
        spec,
    )

    assert report["status"] == "fail"
    assert any(check["name"] == "pineforge_position_sizing" and check["status"] == "fail" for check in report["checks"])


def test_pineforge_guardrail_allows_explicit_cash_sizing_qty() -> None:
    spec = {
        **valid_spec(),
        "position_sizing": "Fixed $1,000",
        "risk_rules": ["Fixed position size of $1,000 per trade"],
    }
    report = validate_pineforge_pine(
        """//@version=6
strategy("RSI Reversal", overlay=true)
cash_per_trade = 1000.0
qty = cash_per_trade / close
if ta.crossover(close, ta.sma(close, 20))
    strategy.entry("Long", strategy.long, qty=qty)
strategy.exit("Exit", "Long", stop=close * 0.98, limit=close * 1.04)
""",
        spec,
    )

    assert not any(check["name"] == "pineforge_position_sizing" and check["status"] == "fail" for check in report["checks"])


def test_create_backtest_plan_pineforge_requires_enabled_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BACKTEST_PINEFORGE_ENABLED", raising=False)
    context, _repository = _tool_context(tmp_path)

    try:
        execute_tool(
            "create_backtest_plan",
            {
                "prompt": "Backtest BTCUSDT 1h",
                "strategy_spec": valid_spec(),
                "pine_code": PINEFORGE_STRATEGY,
                "backtest_config": {"engine": "pineforge"},
            },
            context,
        )
    except Exception as exc:
        assert "backtest preview is disabled" in str(exc)
    else:
        raise AssertionError("pineforge should require explicit enable flag")


def test_run_backtest_preview_pineforge_queues_pine_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    config = {
        "engine": "pineforge",
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

    plan = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Run local preview",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
            "backtest_config": config,
        },
        context,
    )
    output = decide_backtest_preview_approval(
        repository,
        context.artifact_store,
        _auth(),
        conversation_id=context.run.conversation_id,
        approval_id=plan["approval_id"],
        decision="approved",
    )

    job = repository.get_run_job(output["job_id"])
    assert job is not None
    assert job.payload_json["backtest_config"]["engine"] == "pineforge"
    assert job.payload_json["backtest_config"]["exchange"] == "binance"
    assert job.payload_json["pine_code"].startswith("//@version=6")
    assert job.payload_json["auto_chain"]["summary_on_complete"] is True
    assert job.payload_json["auto_chain"]["source_run_id"] == context.run.id
    assert "strategy_" + "logic" not in job.payload_json


def test_variant_lab_queues_comparable_runs_and_comparison_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    base_config = {
        "engine": "pineforge",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }

    output = execute_tool(
        "run_backtest_variant_lab",
            {
                "prompt": "Compare fee assumptions",
                "strategy_spec": valid_spec(),
                "pine_code": PINEFORGE_STRATEGY,
                "base_backtest_config": base_config,
            "variants": [
                {"name": "base"},
                {"name": "higher fee", "backtest_config": {"fee_bps": 20}},
            ],
        },
        context,
    )

    assert len(output["variants"]) == 2
    assert output["variants"][0]["backtest_config"]["fee_bps"] == 10.0
    assert output["variants"][0]["backtest_config"]["exchange"] == "binance"
    assert output["variants"][0]["backtest_config"]["candle_timeframe"] == "1m"
    assert output["variants"][1]["backtest_config"]["fee_bps"] == 20.0
    assert output["variants"][1]["backtest_config"]["candle_timeframe"] == "1m"
    assert output["shared_cache"] is True
    assert output["variants"][0]["cache_key"] == output["variants"][1]["cache_key"]
    assert "execution_semantics" not in output["variants"][0]
    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_variant_comparison"
    for variant in output["variants"]:
        child_run = repository.get_run(_auth(), variant["run_id"])
        assert child_run is not None
        assert child_run.status == "queued"


def test_variant_lab_does_not_mark_cross_exchange_variants_as_shared_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_PINEFORGE_ENABLED", "1")
    context, repository = _tool_context(tmp_path)
    base_config = {
        "engine": "pineforge",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start": "2024-01-01",
        "end": "2024-02-01",
        "initial_capital": 10000,
        "fee_bps": 10,
        "slippage_bps": 5,
        "data_source": "public-readonly-cache",
    }

    output = execute_tool(
        "run_backtest_variant_lab",
        {
            "prompt": "Compare exchanges",
            "strategy_spec": valid_spec(),
            "pine_code": PINEFORGE_STRATEGY,
            "base_backtest_config": base_config,
            "variants": [
                {"name": "binance"},
                {"name": "okx", "backtest_config": {"exchange": "okx"}},
            ],
        },
        context,
    )

    assert output["shared_cache"] is False
    assert output["shared_cache_key"] is None
    assert output["variants"][0]["cache_key"] != output["variants"][1]["cache_key"]
    first_job = repository.get_run_job(output["variants"][0]["job_id"])
    second_job = repository.get_run_job(output["variants"][1]["job_id"])
    assert first_job is not None
    assert second_job is not None
    assert "shared_cache_key" not in first_job.payload_json["chat_tool"]
    assert "shared_cache_key" not in second_job.payload_json["chat_tool"]
