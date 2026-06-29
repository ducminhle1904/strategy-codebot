import pytest

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_READY
from strategy_codebot.server.bot_proposals import BotProposalArtifactUnreadableError
from strategy_codebot.server.bot_proposals import BotProposalDraftInput
from strategy_codebot.server.bot_proposals import BotProposalSourceNotFoundError
from strategy_codebot.server.bot_proposals import build_bot_proposal_create_input
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.repository import BotProposalCreateInput
from strategy_codebot.server.repository import InMemoryConversationRepository
from tests.test_server_security_cost_controls import FakeRedis


AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}


def _strategy_spec() -> dict:
    return {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "name": "BTC trend bot",
        "entry_rules": ["Enter when trend confirms."],
        "exit_rules": ["Exit when trend invalidates."],
        "risk_rules": ["Use capped simulated risk."],
    }


def _source_run(repository) -> str:
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Bots workflow")
    assert conversation is not None
    run = repository.create_run(auth, conversation.id, status="completed", mode="strategy")
    assert run is not None
    spec = repository.create_strategy_spec(auth, run.id, _strategy_spec(), "2026-06")
    assert spec is not None
    return run.id


def _create_input(*, source_conversation_id: str | None, source_run_id: str | None, source_artifact_ids: list[str]) -> BotProposalCreateInput:
    return BotProposalCreateInput(
        status=BOT_PROPOSAL_STATUS_READY,
        source_conversation_id=source_conversation_id,
        source_run_id=source_run_id,
        source_artifact_ids=source_artifact_ids,
        strategy_id="strategy_1",
        strategy_name="BTC bot",
        manifest_json={"name": "BTC bot", "strategy_id": "strategy_1"},
        data_subscriptions_json=[{"symbol": "BTC/USDT", "timeframe": "1h"}],
        broker_connection_id="simulated-broker",
        account_id="paper-account-1",
        risk_policy_id="risk-policy-1",
        readiness_checks_json=["Static contract passed", "No broker execution"],
        missing_inputs_json=[],
    )


def test_bot_proposal_builder_parity_for_api_and_tool_inputs(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext("user-a", "workspace-a")
    run_id = _source_run(repository)
    artifact_store = LocalArtifactStore(tmp_path)

    api_result = build_bot_proposal_create_input(
        auth=auth,
        repository=repository,
        artifact_store=artifact_store,
        draft=BotProposalDraftInput(
            run_id=run_id,
            broker_connection_id="simulated-broker",
            account_id="paper-account-1",
            risk_policy_id="risk-policy-1",
        ),
    )
    tool_result = build_bot_proposal_create_input(
        auth=auth,
        repository=repository,
        artifact_store=artifact_store,
        draft=BotProposalDraftInput(
            fallback_run_id=run_id,
            fallback_conversation_id=api_result.create_input.source_conversation_id,
            broker_connection_id="simulated-broker",
            account_id="paper-account-1",
            risk_policy_id="risk-policy-1",
        ),
    )

    assert tool_result.create_input == api_result.create_input
    assert api_result.create_input.status == BOT_PROPOSAL_STATUS_READY


def test_bot_proposal_builder_uses_manifest_subscriptions_and_name_precedence(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    result = build_bot_proposal_create_input(
        auth=AuthContext("user-a", "workspace-a"),
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            draft=BotProposalDraftInput(
                strategy_spec={"title": "Title fallback bot"},
                strategy_id="strategy_manifest",
                manifest={"data_subscriptions": [{"symbol": "ETH/USDT", "timeframe": "15m"}]},
                broker_connection_id="simulated-broker",
            account_id="paper-account-1",
            risk_policy_id="risk-policy-1",
        ),
    )

    assert result.create_input.strategy_name == "Title fallback bot"
    assert result.create_input.data_subscriptions_json == [{"symbol": "ETH/USDT", "timeframe": "15m"}]
    assert result.missing_inputs == []


def test_bot_proposal_builder_maps_source_errors(tmp_path) -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext("user-a", "workspace-a")

    with pytest.raises(BotProposalSourceNotFoundError):
        build_bot_proposal_create_input(
            auth=auth,
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            draft=BotProposalDraftInput(strategy_artifact_id="art_missing"),
        )

    conversation = repository.create_conversation(auth, "Artifact")
    assert conversation is not None
    run = repository.create_run(auth, conversation.id)
    assert run is not None
    artifact = repository.create_artifact(
        auth,
        run.id,
        kind="pine_file",
        mime_type="application/json",
        display_name="strategy.json",
        storage_key="runs/run_missing/strategy.json",
    )
    assert artifact is not None
    with pytest.raises(BotProposalArtifactUnreadableError):
        build_bot_proposal_create_input(
            auth=auth,
            repository=repository,
            artifact_store=LocalArtifactStore(tmp_path),
            draft=BotProposalDraftInput(strategy_artifact_id=artifact.id),
        )


def test_bot_proposal_create_input_persists_in_memory_and_sqlite() -> None:
    auth = AuthContext("user-a", "workspace-a")
    for repository in (InMemoryConversationRepository(), create_sqlite_repository()):
        conversation = repository.create_conversation(auth, "Persistence")
        assert conversation is not None
        run = repository.create_run(auth, conversation.id)
        assert run is not None
        artifact = repository.create_artifact(
            auth,
            run.id,
            kind="pine_file",
            mime_type="text/x-pine",
            display_name="strategy.pine",
            storage_key="runs/run_1/strategy.pine",
        )
        assert artifact is not None
        proposal = repository.create_bot_proposal(
            auth,
            _create_input(
                source_conversation_id=conversation.id,
                source_run_id=run.id,
                source_artifact_ids=[artifact.id],
            ),
        )

        assert proposal.status == BOT_PROPOSAL_STATUS_READY
        assert proposal.source_conversation_id == conversation.id
        assert proposal.source_run_id == run.id
        assert proposal.source_artifact_ids == [artifact.id]
        assert proposal.manifest_json["strategy_id"] == "strategy_1"
        assert proposal.data_subscriptions_json == [{"symbol": "BTC/USDT", "timeframe": "1h"}]
        assert proposal.readiness_checks_json == ["Static contract passed", "No broker execution"]


def test_bot_proposal_from_strategy_run_confirm_start_creates_paper_runtime() -> None:
    repository = create_sqlite_repository()
    run_id = _source_run(repository)
    client = TestClient(create_app(repository=repository, redis_client=FakeRedis()))

    proposal_response = client.post(
        "/v1/bots/proposals",
        headers=AUTH_A,
        json={
            "run_id": run_id,
            "broker_connection_id": "simulated-broker",
            "account_id": "paper-account-1",
            "risk_policy_id": "risk-policy-1",
            "readiness_checks": ["Static contract passed"],
        },
    )

    assert proposal_response.status_code == 201, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["status"] == "ready"
    assert proposal["source_run_id"] == run_id
    assert proposal["strategy_id"] == run_id
    assert proposal["strategy_name"] == "BTC trend bot"
    assert proposal["data_subscriptions"] == [{"symbol": "BTC/USDT", "timeframe": "1h", "market": "crypto"}]
    assert "No broker execution" in proposal["readiness_checks"]
    assert proposal["runtime_id"] is None

    headers = {**AUTH_A, "Idempotency-Key": "confirm-bot-1"}
    started_response = client.post(
        f"/v1/bots/proposals/{proposal['id']}/confirm-start",
        headers=headers,
        json={},
    )
    replay_response = client.post(
        f"/v1/bots/proposals/{proposal['id']}/confirm-start",
        headers=headers,
        json={},
    )
    fresh_key_response = client.post(
        f"/v1/bots/proposals/{proposal['id']}/confirm-start",
        headers={**AUTH_A, "Idempotency-Key": "confirm-bot-2"},
        json={},
    )

    assert started_response.status_code == 200, started_response.text
    assert replay_response.status_code == 200, replay_response.text
    assert fresh_key_response.status_code == 200, fresh_key_response.text
    assert replay_response.json() == started_response.json()
    assert fresh_key_response.json() == started_response.json()
    started = started_response.json()
    assert started["proposal"]["status"] == "started"
    assert started["proposal"]["runtime_id"] == started["runtime"]["id"]
    assert started["runtime"]["mode"] == "paper"
    assert started["runtime"]["broker_connection_id"] == "simulated-broker"
    assert started["runtime"]["manifest"]["bot_proposal_id"] == proposal["id"]

    runtimes = client.get("/v1/nautilus/runtimes?mode=paper", headers=AUTH_A)
    assert runtimes.status_code == 200, runtimes.text
    assert [runtime["id"] for runtime in runtimes.json()["items"]] == [started["runtime"]["id"]]
    events = client.get(f"/v1/nautilus/runtimes/{started['runtime']['id']}/events", headers=AUTH_A)
    assert [event["type"] for event in events.json()] == ["strategy_loaded"]


def test_bot_proposal_missing_inputs_blocks_confirm_start() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))

    proposal_response = client.post(
        "/v1/bots/proposals",
        headers=AUTH_A,
        json={"strategy_id": "strategy-1", "strategy_name": "Draft bot"},
    )

    assert proposal_response.status_code == 201, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["status"] == "missing_inputs"
    assert set(proposal["missing_inputs"]) == {
        "broker_connection_id",
        "account_id",
        "risk_policy_id",
        "data_subscriptions",
    }

    started_response = client.post(
        f"/v1/bots/proposals/{proposal['id']}/confirm-start",
        headers=AUTH_A,
        json={"broker_connection_id": "sim", "account_id": "acct", "risk_policy_id": "risk"},
    )

    assert started_response.status_code == 422
    assert "data_subscriptions" in started_response.json()["detail"]["missing_inputs"]


def test_bot_proposals_are_tenant_isolated() -> None:
    repository = create_sqlite_repository()
    run_id = _source_run(repository)
    client = TestClient(create_app(repository=repository))

    proposal_response = client.post(
        "/v1/bots/proposals",
        headers=AUTH_A,
        json={
            "run_id": run_id,
            "broker_connection_id": "simulated-broker",
            "account_id": "paper-account-1",
            "risk_policy_id": "risk-policy-1",
        },
    )
    assert proposal_response.status_code == 201, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    assert client.get(f"/v1/bots/proposals/{proposal_id}", headers=AUTH_B).status_code == 404
    assert client.post(f"/v1/bots/proposals/{proposal_id}/confirm-start", headers=AUTH_B, json={}).status_code == 404
