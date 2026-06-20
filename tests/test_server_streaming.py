import re

from fastapi.testclient import TestClient

from strategy_codebot.server import create_app
from strategy_codebot.server.app import _terminal_status_from_sse_frame
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.repository import InMemoryConversationRepository
from strategy_codebot.server.streaming import SSE_EVENT_TYPES
from strategy_codebot.server.streaming import compact_delta_text
from server_helpers import parse_sse as _parse_sse

AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}


def test_deterministic_compact_delta_uses_markdown_friendly_format() -> None:
    text = compact_delta_text()

    assert text.startswith("## Review-only response")
    assert "- Strategy context" in text
    assert "No live trading" in text


def test_streaming_message_returns_sse_and_deterministic_events() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Build a breakout strategy"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(response.text)
    event_types = [frame["event"] for frame in frames]
    assert event_types[0] == "stage.started"
    assert event_types[-1] == "run.completed"
    assert set(event_types).issubset(SSE_EVENT_TYPES)
    assert _persisted_event_types(frames) == [
        "stage.started",
        "tool.started",
        "message.delta",
        "stage.completed",
        "tool.completed",
        "validation.completed",
        "review.completed",
        "run.completed",
    ]
    transient_deltas = [
        frame for frame in frames if frame["event"] == "message.delta" and frame["data"]["sequence"] == 0
    ]
    assert len(transient_deltas) >= 2
    assert all(frame["data"]["payload"]["transient"] for frame in transient_deltas)
    messages = client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A)
    assert messages.status_code == 200, messages.text
    persisted_messages = messages.json()["items"]
    assert [message["role"] for message in persisted_messages] == ["user", "assistant"]
    assert persisted_messages[1]["content"]


def test_persisted_events_compact_streamed_token_deltas() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Build a mean reversion strategy"},
    )
    run_id = _parse_sse(stream.text)[0]["data"]["run_id"]

    replay = client.get(f"/v1/runs/{run_id}/events", headers=AUTH_A)

    assert replay.status_code == 200, replay.text
    frames = _parse_sse(replay.text)
    message_deltas = [frame for frame in frames if frame["event"] == "message.delta"]
    assert len(message_deltas) == 1
    assert message_deltas[0]["data"]["payload"]["compact"] is True
    assert "text" in message_deltas[0]["data"]["payload"]
    assert all(frame["data"]["sequence"] > 0 for frame in frames)

    state = client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A)
    assert state.status_code == 200, state.text
    latest_events = state.json()["latest_run_events"]
    assert [event["sequence"] for event in latest_events] == list(range(1, len(latest_events) + 1))
    assert [event["type"] for event in latest_events] == [frame["event"] for frame in frames]


def test_run_event_replay_supports_last_event_id_by_sequence_and_event_id() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Stream and resume"},
    )
    run_id = _parse_sse(stream.text)[0]["data"]["run_id"]
    all_events = _parse_sse(client.get(f"/v1/runs/{run_id}/events", headers=AUTH_A).text)

    after_sequence = client.get(f"/v1/runs/{run_id}/events", headers={**AUTH_A, "Last-Event-ID": "2"})
    after_event_id = client.get(
        f"/v1/runs/{run_id}/events",
        headers={**AUTH_A, "Last-Event-ID": all_events[2]["data"]["event_id"]},
    )

    assert [frame["data"]["sequence"] for frame in _parse_sse(after_sequence.text)] == list(range(3, len(all_events) + 1))
    assert [frame["data"]["sequence"] for frame in _parse_sse(after_event_id.text)] == list(range(4, len(all_events) + 1))


def test_terminal_status_parser_preserves_failed_and_blocked_stream_status() -> None:
    failed = (
        "id: evt_failed\n"
        "event: run.failed\n"
        "data: {\"event_id\":\"evt_failed\",\"run_id\":\"run_1\",\"sequence\":4,\"payload\":{\"error\":\"ProviderTimeoutError\"}}\n\n"
    )
    blocked = (
        "id: evt_blocked\n"
        "event: run.completed\n"
        "data: {\"event_id\":\"evt_blocked\",\"run_id\":\"run_2\",\"sequence\":5,\"payload\":{\"status\":\"blocked\"}}\n\n"
    )

    assert _terminal_status_from_sse_frame(failed) == "failed"
    assert _terminal_status_from_sse_frame(blocked) == "blocked"


def test_run_progress_snapshot_and_last_event_id() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Show progress"},
    )
    run_id = _parse_sse(stream.text)[0]["data"]["run_id"]
    all_events = _parse_sse(client.get(f"/v1/runs/{run_id}/events", headers=AUTH_A).text)

    progress = _parse_sse(client.get(f"/v1/runs/{run_id}/progress", headers=AUTH_A).text)
    resumed = _parse_sse(
        client.get(
            f"/v1/runs/{run_id}/progress",
            headers={**AUTH_A, "Last-Event-ID": all_events[-2]["data"]["event_id"]},
        ).text
    )

    assert progress[0]["event"] == "progress.snapshot"
    assert progress[0]["data"]["payload"]["event_count"] == len(all_events)
    assert progress[0]["data"]["payload"]["latest_event_id"] == all_events[-1]["data"]["event_id"]
    assert [frame["event"] for frame in resumed] == ["progress.snapshot", "progress.update"]


def test_run_progress_uses_repository_snapshot_boundary() -> None:
    class SpyRepository(InMemoryConversationRepository):
        def __init__(self) -> None:
            super().__init__()
            self.snapshot_calls = 0
            self.summary_calls = 0
            self.artifact_calls = 0

        def get_run_progress_snapshot(self, auth: AuthContext, run_id: str):
            self.snapshot_calls += 1
            return super().get_run_progress_snapshot(auth, run_id)

        def summarize_run_events(self, auth: AuthContext, run_id: str):
            self.summary_calls += 1
            return super().summarize_run_events(auth, run_id)

        def list_artifacts(self, auth: AuthContext, run_id: str):
            self.artifact_calls += 1
            return super().list_artifacts(auth, run_id)

    repository = SpyRepository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Show progress"},
    )
    run_id = _parse_sse(stream.text)[0]["data"]["run_id"]
    repository.summary_calls = 0
    repository.artifact_calls = 0

    progress = client.get(f"/v1/runs/{run_id}/progress", headers=AUTH_A)

    assert progress.status_code == 200, progress.text
    assert repository.snapshot_calls == 1
    assert repository.summary_calls == 1
    assert repository.artifact_calls == 1


def test_cross_tenant_run_event_access_returns_404() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    stream = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true",
        headers=AUTH_A,
        json={"content": "Keep run private"},
    )
    run_id = _parse_sse(stream.text)[0]["data"]["run_id"]

    assert client.get(f"/v1/runs/{run_id}/events", headers=AUTH_B).status_code == 404
    assert client.get(f"/v1/runs/{run_id}/events", headers=AUTH_OTHER_WORKSPACE).status_code == 404
    assert client.get(f"/v1/runs/{run_id}/progress", headers=AUTH_B).status_code == 404
    assert client.get(f"/v1/runs/{run_id}/progress", headers=AUTH_OTHER_WORKSPACE).status_code == 404
    assert client.post(f"/v1/runs/{run_id}/cancel", headers=AUTH_B).status_code == 404
    assert client.post(f"/v1/runs/{run_id}/retry", headers=AUTH_OTHER_WORKSPACE).status_code == 404


def test_cancel_marks_authorized_running_run_cancelled() -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = repository.create_run(AuthContext("user-a", "workspace-a"), conversation["id"], status="running")
    assert run is not None

    response = client.post(f"/v1/runs/{run.id}/cancel", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == run.id
    assert payload["status"] == "cancelled"
    events = repository.list_run_events(AuthContext("user-a", "workspace-a"), run.id)
    assert events is not None
    assert events[-1].type == "run.cancelled"
    assert events[-1].payload == {"status": "cancelled", "reason": "api_cancelled"}


def test_retry_creates_new_authorized_run_linked_to_prior_run() -> None:
    repository = create_sqlite_repository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()
    run = repository.create_run(AuthContext("user-a", "workspace-a"), conversation["id"], status="completed")
    assert run is not None

    response = client.post(f"/v1/runs/{run.id}/retry", headers=AUTH_A)

    assert response.status_code == 201, response.text
    payload = response.json()
    assert re.fullmatch(r"run_[0-9a-f]{32}", payload["id"])
    assert payload["conversation_id"] == conversation["id"]
    assert payload["status"] == "queued"
    assert payload["retry_of_run_id"] == run.id


def test_non_stream_message_endpoint_remains_json_response() -> None:
    client = TestClient(create_app(repository=create_sqlite_repository()))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "plain message"},
    )

    assert response.status_code == 201, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["role"] == "user"




def _persisted_event_types(frames: list[dict]) -> list[str]:
    return [frame["event"] for frame in frames if frame["data"]["sequence"] > 0]
