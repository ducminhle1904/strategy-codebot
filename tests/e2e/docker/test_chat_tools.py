from __future__ import annotations

from .helpers import auth
from .helpers import client
from .helpers import db_rows
from .helpers import parse_sse
from .helpers import wait_for_run_events
from .helpers import write_json


def _stream_agent_message(prompt: str, *, workspace: str) -> tuple[dict[str, str], str, list[dict]]:
    headers = auth(workspace=workspace)
    with client(timeout=60.0) as api:
        conversation = api.post("/v1/conversations", headers=headers, json={"title": prompt[:40]}).json()
        response = api.post(
            f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
            headers=headers,
            json={"content": prompt, "language": "en"},
        )
        assert response.status_code == 200, response.text
        frames = parse_sse(response.text)
        write_json(f"chat-{workspace}.json", frames)
        assert frames[-1]["event"] == "run.completed"
        return headers, conversation["id"], frames


def _tool_output(frames: list[dict], tool_id: str) -> dict:
    for frame in frames:
        if frame["event"] != "tool.completed":
            continue
        payload = frame["data"]["payload"]
        if payload.get("tool_id") == tool_id:
            assert "status" not in payload, payload
            return payload["output"]
    raise AssertionError(f"missing tool.completed for {tool_id}")


def test_chat_tool_creates_backtest_plan() -> None:
    _headers, _conversation_id, frames = _stream_agent_message(
        "Create a local preview plan for BTC 1h.",
        workspace="e2e-chat-plan",
    )
    output = _tool_output(frames, "create_backtest_plan")
    assert "engine" not in output["backtest_config"]
    assert "TradingView proof" in " ".join(output["warnings"])


def test_chat_tool_queues_backtest_preview_and_worker_completes_child_run() -> None:
    headers, _conversation_id, frames = _stream_agent_message(
        "Run and queue a backtest preview now.",
        workspace="e2e-chat-run",
    )
    output = _tool_output(frames, "run_backtest_preview")
    assert output["status"] == "queued"
    assert output["evidence_label"] == "Local sandbox preview evidence only"
    with client(timeout=60.0) as api:
        child_frames = wait_for_run_events(api, headers, output["run_id"])
    assert child_frames[-1]["event"] == "run.completed"
    artifacts = db_rows(
        """
        SELECT id, kind
        FROM artifacts
        WHERE run_id = %s
        ORDER BY created_at, id
        """,
        (output["run_id"],),
    )
    report_artifact = next(artifact for artifact in artifacts if artifact["kind"] == "backtest_report")
    with client(timeout=30.0) as api:
        report = api.get(f"/v1/artifacts/{report_artifact['id']}", headers=headers).json()["content"]
    assert report["execution_semantics"] == "model_generated_pine_pineforge"


def test_chat_variant_lab_queues_multiple_child_runs_with_shared_cache_key() -> None:
    headers, _conversation_id, frames = _stream_agent_message(
        "Create a variant lab with two comparable local preview variants.",
        workspace="e2e-chat-variant",
    )
    output = _tool_output(frames, "run_backtest_variant_lab")
    assert len(output["variants"]) == 2
    assert output["shared_cache_key"]
    with client(timeout=60.0) as api:
        for variant in output["variants"]:
            wait_for_run_events(api, headers, variant["run_id"])
    rows = db_rows(
        """
        SELECT status, count(*) AS count
        FROM run_jobs
        WHERE run_id = ANY(%s)
        GROUP BY status
        """,
        ([variant["run_id"] for variant in output["variants"]],),
    )
    assert rows == [{"status": "completed", "count": 2}]
