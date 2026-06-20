from pathlib import Path

from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.llm_tools import provider_tools
from strategy_codebot.server.repository import InMemoryConversationRepository
from tests.server_helpers import valid_spec


def _auth() -> AuthContext:
    return AuthContext(user_id="user-a", workspace_id="workspace-a", user_tier="free")


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


def test_provider_tools_include_backtest_chat_actions() -> None:
    tool_names = {tool["name"] for tool in provider_tools()}

    assert {
        "create_backtest_plan",
        "run_backtest_preview",
        "run_backtest_variant_lab",
        "create_pinets_preview_plan",
        "create_signals_market_context_plan",
        "create_graph_pipeline_plan",
        "create_sidekick_export_plan",
    } <= tool_names


def test_create_backtest_plan_normalizes_prompt_into_config(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_backtest_plan",
        {
            "prompt": "Backtest ETHUSDT 4h from 2024-01-01 to 2024-03-01 with capital 25000",
            "strategy_spec": valid_spec(),
        },
        context,
    )

    assert output["backtest_config"] == {
        "engine": "backtest-kit",
        "symbol": "ETH/USDT",
        "timeframe": "4h",
        "start": "2024-01-01",
        "end": "2024-03-01",
        "initial_capital": 25000.0,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
        "data_source": "public-readonly-cache",
    }
    assert output["execution_semantics"] == "semantic_strategy_logic"
    assert output["strategy_logic"]["logic_version"] == "backtest-strategy-logic.v1"
    assert output["strategy_logic"]["entry"]["all"][0]["type"] == "crossover"
    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_plan"
    payload = context.artifact_store.read_content(artifact)
    assert payload["strategy_logic"] == output["strategy_logic"]


def test_run_backtest_preview_tool_queues_child_run(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)
    config = {
        "engine": "backtest-kit",
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
        "run_backtest_preview",
        {"strategy_spec": valid_spec(), "backtest_config": config, "prompt": "Run preview"},
        context,
    )

    child_run = repository.get_run(_auth(), output["run_id"])
    assert child_run is not None
    assert child_run.status == "queued"
    assert child_run.mode == "backtest-preview"
    job = repository.get_run_job(output["job_id"])
    assert job is not None
    assert job.payload_json["backtest_config"]["symbol"] == "BTC/USDT"
    assert job.payload_json["strategy_logic"]["logic_version"] == "backtest-strategy-logic.v1"
    assert job.payload_json["runtime"]["allowed_api"] == ["Backtest.run"]
    assert output["execution_semantics"] == "semantic_strategy_logic"


def test_variant_lab_queues_comparable_runs_and_comparison_artifact(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)
    base_config = {
        "engine": "backtest-kit",
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
    assert output["variants"][1]["backtest_config"]["fee_bps"] == 20.0
    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_variant_comparison"
    for variant in output["variants"]:
        child_run = repository.get_run(_auth(), variant["run_id"])
        assert child_run is not None
        assert child_run.status == "queued"


def test_pinets_preview_plan_labels_local_preview_not_tradingview_validation(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_pinets_preview_plan",
        {
            "prompt": "Preview this Pine strategy locally",
            "strategy_spec": valid_spec(),
            "pine_code": "//@version=5\nindicator('demo')\nplot(close, 'Close')",
        },
        context,
    )

    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_pinets_preview"
    payload = context.artifact_store.read_content(artifact)
    assert output["evidence_label"] == "PineTS local preview only"
    assert "TradingView validation" in payload["not_evidence"]


def test_signals_market_context_plan_keeps_model_routing_in_strategy_codebot(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_signals_market_context_plan",
        {"prompt": "Create LLM market context", "symbol": "ethusdt"},
        context,
    )

    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_signals_context"
    payload = context.artifact_store.read_content(artifact)
    assert output["symbol"] == "ETHUSDT"
    assert payload["routing_policy"]["model_routing_owner"] == "strategy-codebot"
    assert payload["routing_policy"]["backtest_kit_ollama"] == "excluded_from_initial_runtime"


def test_graph_pipeline_plan_records_variant_composition(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_graph_pipeline_plan",
        {
            "prompt": "Compose 4h trend and 15m entries",
            "strategy_spec": valid_spec(),
            "timeframes": ["4h", "15m"],
            "variants": ["base", "strict trend"],
        },
        context,
    )

    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_graph_pipeline"
    payload = context.artifact_store.read_content(artifact)
    assert output["node_count"] == 3
    assert payload["variant_composition"]["queue_target"] == "run_backtest_variant_lab"


def test_sidekick_export_plan_is_scaffold_only_not_runtime(tmp_path: Path) -> None:
    context, repository = _tool_context(tmp_path)

    output = execute_tool(
        "create_sidekick_export_plan",
        {
            "prompt": "Export this strategy for full-control scaffold",
            "strategy_spec": valid_spec(),
            "project_name": "my bot!",
        },
        context,
    )

    artifact = repository.get_artifact(_auth(), output["artifact_id"])
    assert artifact is not None
    assert artifact.kind == "backtest_sidekick_export"
    payload = context.artifact_store.read_content(artifact)
    assert output["project_name"] == "my-bot"
    assert payload["runtime_policy"]["api_runtime"] == "blocked"
