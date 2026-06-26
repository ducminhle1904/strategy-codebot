import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.models import Base
from server_helpers import parse_sse
from server_helpers import valid_spec

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}


@dataclass
class FakeLLMClient:
    events: list[LLMClientEvent]
    model: str = "fake-responses-model"

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages: list[dict[str, str]], tools: list[dict]) -> Iterable[LLMClientEvent]:
        return list(self.events)


def test_phase8_schema_adds_request_id_and_feedback_table() -> None:
    migration = Path("migrations/versions/0002_observability_feedback.py").read_text(encoding="utf-8")

    assert "request_id" in Base.metadata.tables["assistant_runs"].c
    assert "feedback" in Base.metadata.tables
    assert "add_column(" in migration
    assert "assistant_runs" in migration
    assert "create_table(" in migration
    assert "feedback" in migration


def test_run_response_and_replayed_events_include_correlation_ids(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        "/v1/runs",
        headers={**AUTH_A, "X-Request-Id": "req_client_observability", "X-Trace-Id": "trace_client_observability"},
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    )

    assert response.status_code == 201, response.text
    run = response.json()
    assert run["request_id"] == "req_client_observability"
    assert run["trace_id"] == "trace_client_observability"
    assert run["conversation_id"] == conversation["id"]

    frames = parse_sse(client.get(f"/v1/runs/{run['id']}/events", headers=AUTH_A).text)
    assert frames[-1]["event"] == "run.completed"
    assert "observability.stage.completed" in [frame["event"] for frame in frames]
    for frame in frames:
        assert frame["data"]["request_id"] == run["request_id"]
        assert frame["data"]["trace_id"] == run["trace_id"]
        assert frame["data"]["conversation_id"] == conversation["id"]
        assert frame["data"]["run_id"] == run["id"]


def test_agent_sse_and_observability_summary_include_usage_tool_and_latency(tmp_path: Path) -> None:
    llm = FakeLLMClient(
        [
            LLMClientEvent(type="usage", model="fake-responses-model", input_tokens=12, output_tokens=4),
            LLMClientEvent(type="tool.call", tool_name="generate_pine", arguments={"strategy_spec": valid_spec()}),
            LLMClientEvent(type="message.delta", text="Generated a review-only Pine artifact."),
        ]
    )
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path, llm_client=llm))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers={**AUTH_A, "X-Request-Id": "req_agent_stream", "X-Trace-Id": "trace_agent_stream"},
        json={"content": "Generate a review-only Pine strategy."},
    )

    assert stream.status_code == 200, stream.text
    frames = parse_sse(stream.text)
    run_id = frames[0]["data"]["run_id"]
    trace_id = frames[0]["data"]["trace_id"]
    assert trace_id == "trace_agent_stream"
    assert all(frame["data"]["request_id"] == "req_agent_stream" for frame in frames)
    assert all(frame["data"]["trace_id"] == trace_id for frame in frames)

    summary = client.get(f"/v1/runs/{run_id}/observability", headers=AUTH_A).json()
    assert summary["request_id"] == "req_agent_stream"
    assert summary["trace_id"] == trace_id
    assert summary["tool_calls"][0]["tool_id"] == "generate_pine"
    assert summary["usage"]["total_tokens"] >= 16
    assert "model" in summary["latency_by_stage"]
    assert "tool" in summary["latency_by_stage"]
    assert "response_finalization" in summary["latency_by_stage"]


def test_harness_evidence_artifact_is_sanitized_and_fetchable(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()

    summary = client.get(f"/v1/runs/{run['id']}/observability", headers=AUTH_A).json()
    artifact_id = summary["harness_evidence_artifact_id"]
    artifact = client.get(f"/v1/artifacts/{artifact_id}", headers=AUTH_A)

    assert artifact.status_code == 200, artifact.text
    payload = artifact.json()
    assert payload["kind"] == "harness_evidence_summary"
    content = payload["content"]
    assert content["request_id"] == run["request_id"]
    assert content["conversation_id"] == run["conversation_id"]
    assert content["run_id"] == run["id"]
    assert content["trace_id"] == run["trace_id"]
    serialized = json.dumps(payload)
    assert "storage_key" not in serialized
    assert "provider_payload" not in serialized
    assert "raw_prompt" not in serialized
    assert str(tmp_path) not in serialized


def test_feedback_endpoint_persists_authorized_metadata_only(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()
    artifact_id = client.get(f"/v1/runs/{run['id']}/observability", headers=AUTH_A).json()["harness_evidence_artifact_id"]

    response = client.post(
        "/v1/feedback",
        headers=AUTH_A,
        json={
            "conversation_id": conversation["id"],
            "run_id": run["id"],
            "artifact_id": artifact_id,
            "rating": "down",
            "category": "safety_correction",
            "correction": "Clarify that this is educational and needs manual TradingView validation.",
        },
    )

    assert response.status_code == 201, response.text
    feedback = response.json()
    assert feedback["id"].startswith("fb_")
    assert feedback["request_id"] == run["request_id"]
    assert feedback["trace_id"] == run["trace_id"]
    assert feedback["rating"] == "down"
    assert "correction" not in feedback

    cross_user = client.post(
        "/v1/feedback",
        headers=AUTH_B,
        json={
            "conversation_id": conversation["id"],
            "run_id": run["id"],
            "rating": "neutral",
            "category": "cross_user",
            "correction": "Should not be accepted.",
        },
    )
    assert cross_user.status_code == 404


def test_cross_user_observability_returns_404(tmp_path: Path) -> None:
    client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = client.post(
        "/v1/runs",
        headers=AUTH_A,
        json={"conversation_id": conversation["id"], "strategy_spec": valid_spec()},
    ).json()

    assert client.get(f"/v1/runs/{run['id']}/observability", headers=AUTH_B).status_code == 404


def test_trading_chat_safety_eval_cases_at_api_boundary(tmp_path: Path) -> None:
    unsafe_llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="This strategy has guaranteed profit.")])
    unsafe_client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path / "unsafe", llm_client=unsafe_llm))
    unsafe_conversation = unsafe_client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    unsafe_stream = unsafe_client.post(
        f"/v1/conversations/{unsafe_conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Can you guarantee profit?"},
    )
    unsafe_events = [frame["event"] for frame in parse_sse(unsafe_stream.text)]
    assert [
        event
        for event in unsafe_events
        if event not in {"model.reasoning.delta", "provider.started", "provider.route"}
    ] == ["chat.response_intent", "chat.suggestions.updated", "policy.blocked", "message.delta", "run.completed"]

    safe_llm = FakeLLMClient([LLMClientEvent(type="message.delta", text="Education only: trading strategies can lose money and need manual validation.")])
    safe_client = TestClient(create_app(repository=create_sqlite_repository(), artifact_root=tmp_path / "safe", llm_client=safe_llm))
    safe_conversation = safe_client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    safe_stream = safe_client.post(
        f"/v1/conversations/{safe_conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_A,
        json={"content": "Explain risk boundaries for a strategy idea."},
    )
    safe_events = [frame["event"] for frame in parse_sse(safe_stream.text)]
    assert "policy.blocked" not in safe_events
    assert safe_events[-1] == "run.completed"
