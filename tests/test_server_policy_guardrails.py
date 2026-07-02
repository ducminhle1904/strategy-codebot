from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.policy import EVIDENCE_EDUCATION
from strategy_codebot.server.policy import EVIDENCE_GENERATED_ARTIFACT
from strategy_codebot.server.policy import EVIDENCE_MANUAL_RUNTIME_PROOF
from strategy_codebot.server.policy import EVIDENCE_STATIC_VALIDATION
from strategy_codebot.server.policy import PolicySubject
from strategy_codebot.server.policy import evaluate_policy
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}


@dataclass
class FakeLLMClient:
    events: list[LLMClientEvent]
    model: str = "fake-responses-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        return list(self.events)


def test_agent_output_policy_block_prevents_unsafe_delta_stream(tmp_path: Path) -> None:
    llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="This has guaranteed profit.")])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "unsafe output"},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    events = [frame["event"] for frame in frames]
    assert [
        event
        for event in events
        if event not in {"model.reasoning.delta", "provider.started"}
        and not event.startswith("model_action.")
        and not event.startswith("classifier.")
        and not event.startswith("prompt_chain.")
        and not event.startswith("agent_loop.")
        and event != "evaluator_optimizer.summary"
    ] == ["chat.response_intent", "chat.suggestions.updated", "policy.blocked", "message.delta", "run.completed"]
    assert frames[-1]["data"]["payload"]["status"] == "blocked"
    message_payloads = [frame["data"]["payload"] for frame in frames if frame["event"] == "message.delta"]
    assert not any("guaranteed profit" in payload.get("text", "").lower() for payload in message_payloads)


def test_forbidden_tool_input_blocks_before_tool_started(tmp_path: Path) -> None:
    spec = {**valid_spec(), "user_notes": "This strategy has guaranteed profit."}
    llm = FakeLLMClient([LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": spec})])
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "unsafe tool"},
    )

    frames = parse_sse(stream.text)
    events = [frame["event"] for frame in frames]
    assert "policy.blocked" in events
    assert "tool.started" not in events
    assert frames[-1]["data"]["payload"]["status"] == "blocked"


def test_policy_blocked_run_does_not_call_dry_run_runner(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    spec = {**valid_spec(), "user_notes": "Backtest success and guaranteed profit."}

    response = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": spec},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["artifacts"] == []
    replay = parse_sse(client.get(f"/v1/runs/{payload['id']}/events", headers=AUTH_A).text)
    event_types = [frame["event"] for frame in replay]
    assert "policy.blocked" in event_types
    assert "tool.started" not in event_types
    assert event_types[-1] == "run.completed"


def test_runtime_success_claim_requires_manual_runtime_proof() -> None:
    static_decision = evaluate_policy(
        PolicySubject(
            surface="artifact.validation_report",
            payload={"message": "Compile success and backtest success."},
            evidence_level=EVIDENCE_STATIC_VALIDATION,
        )
    )
    proof_decision = evaluate_policy(
        PolicySubject(
            surface="artifact.runtime_trace_summary",
            payload={"message": "Compile success and backtest success."},
            evidence_level=EVIDENCE_MANUAL_RUNTIME_PROOF,
        )
    )

    assert not static_decision.allowed
    assert proof_decision.allowed


def test_education_boundary_text_is_allowed() -> None:
    decision = evaluate_policy(
        PolicySubject(
            surface="agent.chat.output",
            payload="Education only: strategies can lose money and require manual validation before runtime claims.",
            evidence_level=EVIDENCE_EDUCATION,
        )
    )

    assert decision.allowed


def test_negated_broker_execution_boundary_text_is_allowed() -> None:
    decision = evaluate_policy(
        PolicySubject(
            surface="tool.generate_pine",
            payload={"strategy_spec": {"constraints": ["No live trading, broker execution, or platform runtime proof was performed."]}},
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )

    assert decision.allowed


def test_positive_broker_execution_request_is_blocked() -> None:
    decision = evaluate_policy(
        PolicySubject(
            surface="tool.generate_pine",
            payload={"strategy_spec": {"user_notes": "Connect broker execution and place orders automatically."}},
            evidence_level=EVIDENCE_GENERATED_ARTIFACT,
        )
    )

    assert not decision.allowed
    assert decision.blocked_finding is not None
    assert decision.blocked_finding.rule_id == "broker_execution"
    assert decision.blocked_finding.code == "policy_violation"


def test_market_data_metadata_is_required() -> None:
    missing = evaluate_policy(
        PolicySubject(
            surface="tool.knowledge_check",
            payload={"market_data": {"source": "fixture", "symbol": "BTCUSDT"}},
        )
    )
    complete = evaluate_policy(
        PolicySubject(
            surface="tool.knowledge_check",
            payload={
                "market_data": {
                    "source": "fixture",
                    "timestamp": "2026-06-17T00:00:00Z",
                    "symbol": "BTCUSDT",
                    "interval": "1h",
                    "timezone": "UTC",
                }
            },
        )
    )

    assert not missing.allowed
    assert missing.blocked_finding is not None
    assert missing.blocked_finding.code == "market_data_metadata_missing"
    assert complete.allowed


def test_artifact_content_policy_block_prevents_content_response(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = repository.create_run(_auth(), conversation["id"], status="completed")
    assert run is not None
    artifact_dir = tmp_path / "runs" / run.id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "unsafe.txt").write_text("This artifact claims guaranteed profit.", encoding="utf-8")
    artifact = repository.create_artifact(
        _auth(),
        run.id,
        kind="manual_checklist",
        mime_type="text/plain",
        display_name="unsafe.txt",
        storage_key=f"runs/{run.id}/unsafe.txt",
        metadata_json={"source": "test"},
    )
    assert artifact is not None

    response = client.get(f"/v1/artifacts/{artifact.id}", headers=AUTH_A)

    assert response.status_code == 422
    replay = parse_sse(client.get(f"/v1/runs/{run.id}/events", headers=AUTH_A).text)
    assert "policy.blocked" in [frame["event"] for frame in replay]


def test_repeated_blocked_artifact_reads_do_not_duplicate_policy_events(tmp_path: Path) -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository, artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    auth = AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
    run = repository.create_run(auth, conversation["id"], status="completed")
    assert run is not None
    artifact_dir = tmp_path / "runs" / run.id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "unsafe.txt").write_text("This artifact claims guaranteed profit.", encoding="utf-8")
    artifact = repository.create_artifact(
        auth,
        run.id,
        kind="manual_checklist",
        mime_type="text/plain",
        display_name="unsafe.txt",
        storage_key=f"runs/{run.id}/unsafe.txt",
        metadata_json={"source": "test"},
    )
    assert artifact is not None

    first = client.get(f"/v1/artifacts/{artifact.id}/preview", headers=AUTH_A)
    second = client.get(f"/v1/artifacts/{artifact.id}/preview", headers=AUTH_A)

    assert first.status_code == 422
    assert second.status_code == 422
    replay = parse_sse(client.get(f"/v1/runs/{run.id}/events", headers=AUTH_A).text)
    assert [frame["event"] for frame in replay].count("policy.blocked") == 1




def _auth():
    from strategy_codebot.server.auth import AuthContext

    return AuthContext(user_id=AUTH_A["X-User-Id"], workspace_id=AUTH_A["X-Workspace-Id"])
