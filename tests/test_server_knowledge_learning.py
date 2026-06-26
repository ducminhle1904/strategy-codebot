from pathlib import Path

from fastapi.testclient import TestClient

from strategy_codebot.knowledge_base import build_knowledge_index
from strategy_codebot.knowledge_base import load_candidates
from strategy_codebot.knowledge_base import search_knowledge
from strategy_codebot.schemas import load_json
from strategy_codebot.schemas import write_json
from strategy_codebot.server import create_app
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlite_repository
from strategy_codebot.server.knowledge_learning import KnowledgeLearningService
from strategy_codebot.server.llm_clients import LLMClientEvent
from strategy_codebot.server.llm_tools import ToolExecutionContext
from strategy_codebot.server.llm_tools import execute_tool
from strategy_codebot.server.model_routing import MODEL_STAGE_KNOWLEDGE_LEARNING_REVIEW

AUTH_HEADERS = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
VIEWER_HEADERS = {**AUTH_HEADERS, "X-Workspace-Role": "viewer"}


class FakeJudgeClient:
    model = "fake-judge"

    def __init__(self) -> None:
        self.routing_contexts: list[dict] = []

    def ensure_configured(self) -> None:
        return None

    def stream(self, *, messages, tools, routing_context=None):
        self.routing_contexts.append(routing_context or {})
        yield LLMClientEvent(
            type="message.delta",
            text='{"generalizable":true,"unsafe_claims":[],"requires_human_review":false,"reason":"general process lesson","confidence":0.92}',
        )


def _isolate_knowledge(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    index_path = tmp_path / "kb" / "index.json"
    candidates_path = tmp_path / "kb" / "candidates.json"
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_CANDIDATES_PATH", str(candidates_path))
    monkeypatch.delenv("STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL", raising=False)
    return index_path, candidates_path


def _repository_with_run(tmp_path: Path):
    repository = create_sqlite_repository()
    auth = AuthContext("user-a", "workspace-a")
    conversation = repository.create_conversation(auth, "Knowledge")
    assert conversation is not None
    run = repository.create_run(auth, conversation.id, status="running")
    assert run is not None
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    return repository, auth, run, artifact_store


def test_knowledge_candidates_list_empty_when_store_missing(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    response = client.get("/v1/knowledge/candidates", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "pass", "store": "local_json", "candidates": []}


def test_knowledge_candidate_approve_promotes_sanitized_chunk(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    create_response = client.post(
        "/v1/knowledge/candidates",
        headers=AUTH_HEADERS,
        json={
            "lesson": "Use confirmed bars before promoting a liquidity sweep setup.",
            "evidence_ref": "run:run_test123:knowledge_proposal",
            "candidate_type": "procedural",
            "confidence": "high",
        },
    )
    candidate_payload = create_response.json()

    assert create_response.status_code == 201
    assert "lesson" not in candidate_payload
    assert candidate_payload["status"] == "needs_review"
    assert len(load_candidates(candidates_path)["candidates"]) == 1

    approve_response = client.post(
        f"/v1/knowledge/candidates/{candidate_payload['candidate_id']}/approve",
        headers=AUTH_HEADERS,
    )
    result = search_knowledge("confirmed bars liquidity sweep", index_path=index_path)

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert load_candidates(candidates_path)["candidates"][0]["status"] == "approved"
    assert any(chunk["source_type"] == "approved_candidate" for chunk in result["retrieved_chunks"])


def test_knowledge_candidate_reject_does_not_promote(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))
    create_response = client.post(
        "/v1/knowledge/candidates",
        headers=AUTH_HEADERS,
        json={
            "lesson": "Prefer explicit invalidation before accepting a breakout setup.",
            "evidence_ref": "run:run_test123:knowledge_proposal",
        },
    )
    candidate_id = create_response.json()["candidate_id"]

    reject_response = client.post(f"/v1/knowledge/candidates/{candidate_id}/reject", headers=AUTH_HEADERS)

    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert load_candidates(candidates_path)["candidates"][0]["status"] == "rejected"
    assert not any(
        item["source_type"] == "approved_candidate" for item in load_json(index_path).get("items", [])
    )


def test_knowledge_candidate_approval_requires_owner_or_admin(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    response = client.post(
        "/v1/knowledge/candidates/candidate-missing/approve",
        headers=VIEWER_HEADERS,
    )

    assert response.status_code == 403


def test_knowledge_candidate_auto_review_requires_owner_or_admin(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    response = client.post(
        "/v1/knowledge/candidates/candidate-missing/auto-review",
        headers=VIEWER_HEADERS,
    )

    assert response.status_code == 403


def test_knowledge_candidate_create_rejects_invalid_type(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    response = client.post(
        "/v1/knowledge/candidates",
        headers=AUTH_HEADERS,
        json={
            "lesson": "Keep validation evidence bounded.",
            "evidence_ref": "run:run_test123:knowledge_proposal",
            "candidate_type": "unknown_type",
        },
    )

    assert response.status_code == 422


def test_run_knowledge_learning_extracts_candidates_from_api_run_dir(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(
        artifact_store.run_dir(run.id) / "eval-report.json",
        {
            "status": "fail",
            "cases": [
                {
                    "id": "case-a",
                    "status": "fail",
                    "failure_class": "static_validation_failed",
                    "failure_stage": "final_gate",
                    "validation_failures": [{"name": "version_header", "status": "fail"}],
                }
            ],
        },
    )
    client = TestClient(create_app(repository=repository, artifact_root=artifact_store.root))

    first = client.post(f"/v1/runs/{run.id}/knowledge-learning", headers=AUTH_HEADERS, json={"approval_mode": "manual"})
    second = client.post(f"/v1/runs/{run.id}/knowledge-learning", headers=AUTH_HEADERS, json={"approval_mode": "manual"})
    events = repository.list_run_events(auth, run.id)

    assert first.status_code == 200
    assert first.json()["proposed_count"] == 1
    assert first.json()["candidates"][0]["status"] == "needs_review"
    assert first.json()["candidates"][0]["evidence_ref"] == f"run:{run.id}:eval-report.json"
    assert str(tmp_path) not in first.json()["candidates"][0]["evidence_ref"]
    assert second.status_code == 200
    assert len(load_candidates(candidates_path)["candidates"]) == 1
    assert events is not None
    assert "knowledge.learning.completed" in [event.type for event in events]
    assert "knowledge.candidate.created" in [event.type for event in events]
    assert [event.type for event in events].count("knowledge.candidate.created") == 1


def test_run_knowledge_learning_guarded_auto_promotes_eligible_candidate(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(
        artifact_store.run_dir(run.id) / "eval-report.json",
        {
            "status": "fail",
            "cases": [
                {
                    "id": "case-a",
                    "status": "fail",
                    "failure_class": "static_validation_failed",
                    "failure_stage": "final_gate",
                    "validation_failures": [{"name": "version_header", "status": "fail"}],
                }
            ],
        },
    )
    client = TestClient(create_app(repository=repository, artifact_root=artifact_store.root))

    response = client.post(f"/v1/runs/{run.id}/knowledge-learning", headers=AUTH_HEADERS, json={"approval_mode": "guarded-auto"})
    body = response.json()
    events = repository.list_run_events(auth, run.id)
    result = search_knowledge("version header Pine repair", index_path=index_path)

    assert response.status_code == 200
    assert body["promoted_count"] == 1
    assert body["promoted"][0]["status"] == "auto_approved"
    assert body["promoted"][0]["promotion_decision"] == "auto_approved"
    assert body["promoted"][0]["quality_score"] == 1.0
    assert body["promoted"][0]["gate_summary"]
    assert load_candidates(candidates_path)["candidates"][0]["status"] == "auto_approved"
    assert any(chunk["source_type"] == "approved_candidate" for chunk in result["retrieved_chunks"])
    assert events is not None
    event_types = [event.type for event in events]
    assert "knowledge.candidate.auto_reviewed" in event_types
    assert "knowledge.candidate.auto_approved" in event_types


def test_guarded_auto_uses_route_aware_llm_judge_when_enabled(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    repository, _auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(
        artifact_store.run_dir(run.id) / "eval-report.json",
        {
            "status": "fail",
            "cases": [
                {
                    "id": "case-a",
                    "status": "fail",
                    "failure_class": "static_validation_failed",
                    "failure_stage": "final_gate",
                    "validation_failures": [{"name": "version_header", "status": "fail"}],
                }
            ],
        },
    )
    llm_client = FakeJudgeClient()
    client = TestClient(create_app(repository=repository, artifact_root=artifact_store.root, llm_client=llm_client))

    response = client.post(f"/v1/runs/{run.id}/knowledge-learning", headers=AUTH_HEADERS, json={"approval_mode": "guarded-auto"})
    candidate = load_candidates(candidates_path)["candidates"][0]

    assert response.status_code == 200
    assert response.json()["promoted_count"] == 1
    assert candidate["status"] == "auto_approved"
    assert llm_client.routing_contexts
    assert llm_client.routing_contexts[0]["stage"] == MODEL_STAGE_KNOWLEDGE_LEARNING_REVIEW


def test_knowledge_auto_review_endpoint_returns_sanitized_gate_summary(monkeypatch, tmp_path: Path) -> None:
    index_path, _ = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    evidence_path = tmp_path / "runs" / "run-01" / "eval-report.json"
    write_json(evidence_path, {"status": "fail"})
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))
    create_response = client.post(
        "/v1/knowledge/candidates",
        headers=AUTH_HEADERS,
        json={
            "lesson": "Manual proposal should not be promoted without extractor lesson kind metadata.",
            "evidence_ref": str(evidence_path),
            "candidate_type": "procedural",
            "confidence": "high",
            "metadata": {"learning": {"evidence_count": 1}},
        },
    )
    candidate_id = create_response.json()["candidate_id"]

    response = client.post(f"/v1/knowledge/candidates/{candidate_id}/auto-review", headers=AUTH_HEADERS)
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "needs_review"
    assert body["promotion_decision"] == "needs_review"
    assert body["review_required_reason"] == "lesson_kind_requires_review"
    assert body["gate_summary"] == ["safety:pass", "trading_claim_boundary:pass", "source_evidence:pass", "lesson_kind:fail"]
    assert "lesson" not in body
    assert str(tmp_path) not in str(body)


def test_knowledge_auto_review_batch_rejects_missing_candidate_ids(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    client = TestClient(create_app(artifact_root=tmp_path / "artifacts"))

    response = client.post(
        "/v1/knowledge/candidates/auto-review",
        headers=AUTH_HEADERS,
        json={"candidate_ids": ["candidate-missing"]},
    )

    assert response.status_code == 404


def test_run_knowledge_learning_needs_review_emits_review_sequence(monkeypatch, tmp_path: Path) -> None:
    index_path, _ = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(
        artifact_store.run_dir(run.id) / "backtest-report.json",
        {
            "status": "completed",
            "robustness_report": {
                "status": "warn",
                "checks": {
                    "sample_size": {
                        "status": "warn",
                        "message": "Low closed-trade sample; keep the result in manual review.",
                    }
                },
            },
        },
    )
    client = TestClient(create_app(repository=repository, artifact_root=artifact_store.root))

    response = client.post(f"/v1/runs/{run.id}/knowledge-learning", headers=AUTH_HEADERS, json={"approval_mode": "guarded-auto"})
    events = repository.list_run_events(auth, run.id)

    assert response.status_code == 200
    assert response.json()["skipped_count"] == 1
    assert events is not None
    event_types = [event.type for event in events]
    assert "knowledge.candidate.auto_reviewed" in event_types
    assert "knowledge.candidate.needs_review" in event_types


def test_auto_learning_guarded_auto_promotes_without_manual_review(monkeypatch, tmp_path: Path) -> None:
    index_path, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    build_knowledge_index(index_path=index_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(
        artifact_store.run_dir(run.id) / "eval-report.json",
        {
            "status": "fail",
            "cases": [
                {
                    "id": "case-a",
                    "status": "fail",
                    "failure_class": "static_validation_failed",
                    "failure_stage": "final_gate",
                    "validation_failures": [{"name": "version_header", "status": "fail"}],
                }
            ],
        },
    )

    KnowledgeLearningService(repository, artifact_store).maybe_extract_run_candidates(auth, run)
    events = repository.list_run_events(auth, run.id)

    assert load_candidates(candidates_path)["candidates"][0]["status"] == "auto_approved"
    assert events is not None
    assert "knowledge.candidate.auto_approved" in [event.type for event in events]


def test_knowledge_proposal_tool_creates_artifact_and_review_candidate(monkeypatch, tmp_path: Path) -> None:
    _, candidates_path = _isolate_knowledge(monkeypatch, tmp_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    context = ToolExecutionContext(repository=repository, artifact_store=artifact_store, auth=auth, run=run)

    result = execute_tool(
        "knowledge_proposal",
        {"summary": "Preserve Pine v6 headers before applying validation repair."},
        context,
    )
    events = repository.list_run_events(auth, run.id)

    assert result["artifact_id"]
    assert result["candidate_id"]
    assert result["status"] == "needs_review"
    assert (artifact_store.run_dir(run.id) / "knowledge-proposal.json").exists()
    assert load_json(artifact_store.run_dir(run.id) / "knowledge-proposal.json")["candidate_id"] == result["candidate_id"]
    assert len(load_candidates(candidates_path)["candidates"]) == 1
    assert events is not None
    assert "artifact.created" in [event.type for event in events]
    assert "knowledge.candidate.created" in [event.type for event in events]


def test_auto_learning_failure_emits_event_without_failing_run(monkeypatch, tmp_path: Path) -> None:
    _isolate_knowledge(monkeypatch, tmp_path)
    repository, auth, run, artifact_store = _repository_with_run(tmp_path)
    write_json(artifact_store.run_dir(run.id) / "eval-report.json", {"cases": []})

    def broken_learn(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("strategy_codebot.server.knowledge_learning.learn_knowledge_from_run", broken_learn)

    KnowledgeLearningService(repository, artifact_store).maybe_extract_run_candidates(auth, run)
    events = repository.list_run_events(auth, run.id)

    assert events is not None
    assert events[-1].type == "knowledge.learning.failed"
    assert events[-1].payload["message"] == "Knowledge learning extraction failed."
