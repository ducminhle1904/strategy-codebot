from pathlib import Path

from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.repository import InMemoryConversationRepository


def _auth() -> AuthContext:
    return AuthContext(user_id="user-a", workspace_id="workspace-a", user_tier="free")


def _context(tmp_path: Path) -> tuple[ToolExecutionContext, InMemoryConversationRepository]:
    repository = InMemoryConversationRepository()
    conversation = repository.create_conversation(_auth(), title="Bots chat")
    assert conversation is not None
    run = repository.create_run(_auth(), conversation.id, status="running", mode="agent")
    assert run is not None
    spec = repository.create_strategy_spec(
        _auth(),
        run.id,
        {
            "target_platform": "pine_v6",
            "script_type": "strategy",
            "market": "crypto",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "name": "BTC trend bot",
            "entry_rules": ["Enter when trend confirms."],
            "exit_rules": ["Exit when trend invalidates."],
            "risk_rules": ["Use capped simulated risk."],
        },
        "2026-06",
    )
    assert spec is not None
    return (
        ToolExecutionContext(
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            auth=_auth(),
            run=run,
        ),
        repository,
    )


def test_draft_bot_tool_creates_proposal_without_starting_runtime(tmp_path: Path) -> None:
    context, repository = _context(tmp_path)

    result = execute_tool(
        "draft_bot",
        {
            "broker_connection_id": "simulated-broker",
            "account_id": "paper-account-1",
            "risk_policy_id": "risk-policy-1",
            "readiness_checks": ["Static contract passed"],
        },
        context,
    )

    assert result["status"] == "ready"
    assert result["next_action"] == "review_setup"
    assert result["bot_proposal"]["broker_connection_id"] == "simulated-broker"
    assert result["bot_proposal"]["account_id"] == "paper-account-1"
    assert result["bot_proposal"]["risk_policy_id"] == "risk-policy-1"
    assert result["bot_proposal"]["strategy_name"] == "BTC trend bot"
    assert result["bot_proposal"]["data_subscriptions"] == [
        {"symbol": "BTC/USDT", "timeframe": "1h", "market": "crypto"}
    ]
    assert result["bot_proposal"]["no_broker_execution"] is True
    assert repository.list_nautilus_runtimes(_auth(), mode="paper") == []


def test_draft_bot_tool_uses_manifest_subscriptions_and_title(tmp_path: Path) -> None:
    context, repository = _context(tmp_path)

    result = execute_tool(
        "draft_bot",
        {
            "strategy_spec": {"title": "Manifest-only bot"},
            "manifest": {"data_subscriptions": [{"symbol": "ETH/USDT", "timeframe": "15m"}]},
            "broker_connection_id": "simulated-broker",
            "account_id": "paper-account-1",
            "risk_policy_id": "risk-policy-1",
        },
        context,
    )

    assert result["status"] == "ready"
    assert result["bot_proposal"]["strategy_name"] == "Manifest-only bot"
    assert result["bot_proposal"]["data_subscriptions"] == [{"symbol": "ETH/USDT", "timeframe": "15m"}]
    assert repository.list_nautilus_runtimes(_auth(), mode="paper") == []


def test_bot_status_and_event_tools_are_read_only(tmp_path: Path) -> None:
    context, repository = _context(tmp_path)
    draft = execute_tool(
        "draft_bot",
        {
            "broker_connection_id": "simulated-broker",
            "account_id": "paper-account-1",
            "risk_policy_id": "risk-policy-1",
        },
        context,
    )
    proposal_id = draft["proposal_id"]
    runtime = repository.upsert_nautilus_runtime(
        _auth(),
        runtime_key="runtime-key-1",
        broker_connection_id="simulated-broker",
        account_id="paper-account-1",
        mode="paper",
        risk_policy_id="risk-policy-1",
        strategy_id=draft["bot_proposal"]["strategy_id"],
        manifest_json={"name": "BTC trend bot", "bot_proposal_id": proposal_id},
        data_subscriptions_json=draft["bot_proposal"]["data_subscriptions"],
    )
    repository.mark_bot_proposal_started(_auth(), proposal_id, runtime_id=runtime.id)
    repository.append_nautilus_runtime_event(
        _auth(),
        runtime.id,
        "risk_block",
        {"reason": "simulated risk guard"},
    )

    status = execute_tool("get_bot_status", {"proposal_id": proposal_id}, context)
    bots = execute_tool("list_bots", {"limit": 10}, context)
    events = execute_tool("list_bot_events", {"runtime_id": runtime.id}, context)

    assert status["status"] == "ok"
    assert status["proposal"]["runtime_id"] == runtime.id
    assert status["runtime"]["no_broker_execution"] is True
    assert bots["bots"] == [status["runtime"]]
    assert events["events"][0]["type"] == "risk_block"
