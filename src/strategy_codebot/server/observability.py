from time import perf_counter
from typing import Any

from strategy_codebot.schemas import write_json
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.redaction import redact_value
from strategy_codebot.server.repository import ArtifactRecord
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository

OBSERVABILITY_EVENT = "observability.stage.completed"
STAGE_STARTED_EVENT = "stage.started"
STAGE_COMPLETED_EVENT = "stage.completed"
HARNESS_EVIDENCE_KIND = "harness_evidence_summary"
HARNESS_EVIDENCE_PATH = "harness-evidence-summary.json"


class StageTimer:
    def __init__(self) -> None:
        self._started = perf_counter()

    def elapsed_ms(self) -> int:
        return int((perf_counter() - self._started) * 1000)


def append_stage_event(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    stage: str,
    duration_ms: int,
    *,
    status: str = "completed",
) -> None:
    payload = {"stage": stage, "duration_ms": max(0, duration_ms), "status": status}
    repository.append_run_event(auth, run.id, STAGE_COMPLETED_EVENT, payload)
    repository.append_run_event(
        auth,
        run.id,
        OBSERVABILITY_EVENT,
        payload,
    )


def append_stage_started_event(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    stage: str,
    *,
    status: str = "running",
) -> None:
    repository.append_run_event(
        auth,
        run.id,
        STAGE_STARTED_EVENT,
        {"stage": stage, "status": status},
    )


def build_observability_summary(
    repository: ConversationRepository,
    auth: AuthContext,
    run_id: str,
) -> dict[str, Any] | None:
    run = repository.get_run(auth, run_id)
    if run is None:
        return None
    events = repository.list_run_events(auth, run.id) or []
    tool_calls = repository.list_tool_calls(auth, run.id) or []
    policy_findings = repository.list_policy_findings(auth, run.id) or []
    usage = repository.list_usage_ledger(auth, run.id) or []
    artifacts = repository.list_artifacts(auth, run.id) or []
    latency_by_stage: dict[str, int] = {}
    for event in events:
        if event.type != OBSERVABILITY_EVENT or not isinstance(event.payload, dict):
            continue
        stage = event.payload.get("stage")
        duration = event.payload.get("duration_ms")
        if isinstance(stage, str) and isinstance(duration, int):
            latency_by_stage[stage] = latency_by_stage.get(stage, 0) + duration
    input_tokens = sum(record.input_tokens for record in usage)
    output_tokens = sum(record.output_tokens for record in usage)
    cost_estimate = sum(record.cost_estimate_usd or 0 for record in usage)
    summary = {
        "request_id": run.request_id,
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "trace_id": run.trace_id,
        "status": run.status,
        "event_count": len(events),
        "artifact_count": len(artifacts),
        "tool_calls": [
            {
                "id": record.id,
                "tool_id": record.tool_id,
                "status": record.status,
                "created_at": record.created_at.isoformat(),
                "started_at": record.started_at.isoformat() if record.started_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            }
            for record in tool_calls
        ],
        "policy_findings": [
            {
                "id": record.id,
                "severity": record.severity,
                "code": record.code,
                "message": record.message,
                "created_at": record.created_at.isoformat(),
            }
            for record in policy_findings
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_estimate_usd": cost_estimate,
        },
        "latency_by_stage": latency_by_stage,
    }
    return redact_value(summary)


def ensure_harness_evidence_artifact(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    run_id: str,
    summary: dict[str, Any],
) -> ArtifactRecord | None:
    run = repository.get_run(auth, run_id)
    if run is None:
        return None
    existing = repository.list_artifacts(auth, run.id) or []
    for artifact in existing:
        if artifact.kind == HARNESS_EVIDENCE_KIND:
            return artifact
    payload = {
        "request_id": run.request_id,
        "conversation_id": run.conversation_id,
        "run_id": run.id,
        "trace_id": run.trace_id,
        "outcome": run.status,
        "events": summary.get("event_count", 0),
        "tool_calls": summary.get("tool_calls", []),
        "policy_findings": summary.get("policy_findings", []),
        "usage": summary.get("usage", {}),
        "latency_by_stage": summary.get("latency_by_stage", {}),
        "verification_refs": ["run_events", "tool_calls", "policy_findings", "usage_ledger"],
    }
    write_json(artifact_store.run_dir(run.id) / HARNESS_EVIDENCE_PATH, redact_value(payload))
    artifact = repository.create_artifact(
        auth,
        run.id,
        kind=HARNESS_EVIDENCE_KIND,
        mime_type="application/json",
        display_name=HARNESS_EVIDENCE_PATH,
        storage_key=artifact_store.storage_key(run.id, HARNESS_EVIDENCE_PATH),
        metadata_json={"source": "api_observability", "trace_id": run.trace_id, "request_id": run.request_id},
    )
    if artifact is not None:
        repository.append_run_event(
            auth,
            run.id,
            "artifact.created",
            {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
        )
    return artifact
