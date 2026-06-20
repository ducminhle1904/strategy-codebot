import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strategy_codebot.knowledge_context import KNOWLEDGE_CONTEXT_PATH
from strategy_codebot.live import LIVE_ERROR_PATH
from strategy_codebot.live import LIVE_WORKFLOW_TRACE_PATH
from strategy_codebot.live import MARKET_RESEARCH_PATH
from strategy_codebot.live import DEFAULT_USER_TIER, USER_TIERS, LiveRunOptions
from strategy_codebot.quality import QUALITY_REPORT_PATH
from strategy_codebot.review import REVIEW_MODE_PARALLEL, REVIEW_REPORT_PATH
from strategy_codebot.runner import run_strategy
from strategy_codebot.schemas import write_json
from strategy_codebot.tool_runtime import POLICY_OBSERVE, RUNTIME_SUMMARY_PATH
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.observability import StageTimer
from strategy_codebot.server.observability import append_stage_event
from strategy_codebot.server.observability import append_stage_started_event
from strategy_codebot.server.repository import ArtifactRecord
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.run_modes import RUN_MODE_DRY_RUN
from strategy_codebot.server.run_modes import RUN_MODE_LIVE_GENERATION

STRATEGY_SPEC_SCHEMA_VERSION = "strategy-spec.schema.json"
VALIDATION_REPORT_PATH = "validation-report.json"
MANUAL_CHECKLIST_PATH = "manual-tradingview-checklist.md"
PINE_STRATEGY_PATH = "pine/strategy.pine"

RUNNER_ARTIFACTS = (
    ("pine_file", "text/plain", "strategy.pine", PINE_STRATEGY_PATH),
    ("validation_report", "application/json", VALIDATION_REPORT_PATH, VALIDATION_REPORT_PATH),
    ("review_report", "application/json", REVIEW_REPORT_PATH, REVIEW_REPORT_PATH),
    ("manual_checklist", "text/markdown", MANUAL_CHECKLIST_PATH, MANUAL_CHECKLIST_PATH),
    ("runtime_trace_summary", "application/json", RUNTIME_SUMMARY_PATH, RUNTIME_SUMMARY_PATH),
)
LIVE_RUNNER_ARTIFACTS = (
    *RUNNER_ARTIFACTS,
    ("agent_run", "application/json", "agent-run.json", "agent-run.json"),
    ("live_metadata", "application/json", "live-metadata.json", "live-metadata.json"),
    ("live_workflow_trace", "application/json", LIVE_WORKFLOW_TRACE_PATH, LIVE_WORKFLOW_TRACE_PATH),
    ("live_error", "application/json", LIVE_ERROR_PATH, LIVE_ERROR_PATH),
    ("market_research", "application/json", "market-research.json", MARKET_RESEARCH_PATH),
    ("quality_report", "application/json", QUALITY_REPORT_PATH, QUALITY_REPORT_PATH),
    ("knowledge_context", "application/json", KNOWLEDGE_CONTEXT_PATH, KNOWLEDGE_CONTEXT_PATH),
)


@dataclass(frozen=True)
class RunnerIntegrationResult:
    run: AssistantRunRecord
    artifacts: list[ArtifactRecord]


def execute_dry_run(
    *,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    conversation_id: str,
    strategy_spec: dict[str, Any],
    existing_run: AssistantRunRecord | None = None,
) -> RunnerIntegrationResult | None:
    return _execute_runner(
        repository=repository,
        artifact_store=artifact_store,
        auth=auth,
        conversation_id=conversation_id,
        strategy_spec=strategy_spec,
        existing_run=existing_run,
        event_mode=RUN_MODE_DRY_RUN,
        runner_mode=RUN_MODE_DRY_RUN,
        input_summary="Strategy spec accepted for dry-run review artifact generation.",
        artifact_catalog=RUNNER_ARTIFACTS,
        artifact_source="runner_dry_run",
    )


def execute_live_generation(
    *,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    conversation_id: str,
    strategy_spec: dict[str, Any],
    existing_run: AssistantRunRecord | None = None,
    web_search: str = "auto",
) -> RunnerIntegrationResult | None:
    return _execute_runner(
        repository=repository,
        artifact_store=artifact_store,
        auth=auth,
        conversation_id=conversation_id,
        strategy_spec=strategy_spec,
        existing_run=existing_run,
        event_mode=RUN_MODE_LIVE_GENERATION,
        runner_mode="live",
        input_summary=f"Live-generation review route for tier {_auth_user_tier(auth)}.",
        artifact_catalog=LIVE_RUNNER_ARTIFACTS,
        artifact_source="runner_live_generation",
        prompt=_live_generation_prompt(strategy_spec),
        live_options=LiveRunOptions(
            save_raw_provider=False,
            user_tier=_auth_user_tier(auth),
            user_id=auth.user_id,
            workspace_id=auth.workspace_id,
            web_search=web_search,
        ),
    )


def _execute_runner(
    *,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    conversation_id: str,
    strategy_spec: dict[str, Any],
    event_mode: str,
    runner_mode: str,
    input_summary: str,
    artifact_catalog: tuple[tuple[str, str, str, str], ...],
    artifact_source: str,
    existing_run: AssistantRunRecord | None = None,
    prompt: str | None = None,
    live_options: LiveRunOptions | None = None,
) -> RunnerIntegrationResult | None:
    run = existing_run or repository.create_run(auth, conversation_id, status="running")
    if run is None:
        return None
    repository.create_strategy_spec(auth, run.id, strategy_spec, STRATEGY_SPEC_SCHEMA_VERSION)
    repository.append_run_event(
        auth,
        run.id,
        "tool.started",
        {
            "tool_id": "strategy_codebot.runner.run_strategy",
            "label": "Generate review artifact",
            "mode": event_mode,
            "input_summary": input_summary,
        },
    )

    out_dir = artifact_store.run_dir(run.id)
    spec_path = out_dir / "strategy-spec.json"
    write_json(spec_path, strategy_spec)

    try:
        runner_timer = StageTimer()
        append_stage_started_event(repository, auth, run, "runner")
        result = run_strategy(
            spec_path=spec_path,
            prompt=prompt,
            mode=runner_mode,
            out_dir=out_dir,
            review=REVIEW_MODE_PARALLEL,
            record_harness=False,
            runtime_trace=True,
            policy=POLICY_OBSERVE,
            live_options=live_options,
        )
        result_status = result.get("status")
        repository.append_run_event(
            auth,
            run.id,
            "tool.completed",
            {
                "tool_id": "strategy_codebot.runner.run_strategy",
                "label": "Generate review artifact",
                "status": result_status,
                "mode": event_mode,
                "output_summary": f"Runner finished with status {result_status}.",
            },
        )
        append_stage_event(repository, auth, run, "runner", runner_timer.elapsed_ms())
        artifact_timer = StageTimer()
        append_stage_started_event(repository, auth, run, "artifact")
        artifacts = _persist_runner_artifacts(
            repository,
            artifact_store,
            auth,
            run,
            out_dir,
            artifact_catalog=artifact_catalog,
            source=artifact_source,
        )
        append_stage_event(repository, auth, run, "artifact", artifact_timer.elapsed_ms())
        report_timer = StageTimer()
        append_stage_started_event(repository, auth, run, "validation")
        _persist_reports_and_events(repository, auth, run, out_dir)
        append_stage_event(repository, auth, run, "validation", report_timer.elapsed_ms())
        append_stage_started_event(repository, auth, run, "review")
        append_stage_event(repository, auth, run, "review", 0)
        completed = repository.set_run_status(auth, run.id, "completed")
        final_run = completed if completed is not None else run
        repository.append_run_event(auth, run.id, "run.completed", {"status": result_status, "mode": event_mode})
        return RunnerIntegrationResult(run=final_run, artifacts=artifacts)
    except Exception as exc:
        failed = repository.set_run_status(auth, run.id, "failed")
        repository.append_run_event(
            auth,
            run.id,
            "run.failed",
            {"error": exc.__class__.__name__, "message": str(exc), "mode": event_mode},
        )
        return RunnerIntegrationResult(run=failed if failed is not None else run, artifacts=[])


def _persist_runner_artifacts(
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    auth: AuthContext,
    run: AssistantRunRecord,
    out_dir: Path,
    *,
    artifact_catalog: tuple[tuple[str, str, str, str], ...],
    source: str,
) -> list[ArtifactRecord]:
    artifacts: list[ArtifactRecord] = []
    artifact_events: list[tuple[str, dict | None]] = []
    for kind, mime_type, display_name, relative_path in artifact_catalog:
        path = out_dir / relative_path
        if not path.exists():
            continue
        artifact = repository.create_artifact(
            auth,
            run.id,
            kind=kind,
            mime_type=mime_type,
            display_name=display_name,
            storage_key=artifact_store.storage_key(run.id, relative_path),
            metadata_json={"source": source},
        )
        if artifact is None:
            continue
        artifacts.append(artifact)
        artifact_events.append(
            (
                "artifact.created",
                {"artifact_id": artifact.id, "kind": artifact.kind, "display_name": artifact.display_name},
            )
        )
    repository.append_run_events(auth, run.id, artifact_events)
    return artifacts


def _live_generation_prompt(strategy_spec: dict[str, Any]) -> str:
    symbol = strategy_spec.get("symbol", "the requested market")
    timeframe = strategy_spec.get("timeframe", "the requested timeframe")
    return (
        "Generate reviewable strategy artifacts from this validated strategy spec. "
        "Do not place live trades, call brokers, call exchanges, or claim profitability. "
        f"Target symbol: {symbol}. Timeframe: {timeframe}."
    )


def _server_user_tier() -> str:
    value = os.getenv("STRATEGY_CODEBOT_SERVER_USER_TIER", DEFAULT_USER_TIER)
    return value if value in USER_TIERS else DEFAULT_USER_TIER


def _auth_user_tier(auth: AuthContext) -> str:
    return auth.user_tier if auth.user_tier in USER_TIERS else _server_user_tier()


def _persist_reports_and_events(
    repository: ConversationRepository,
    auth: AuthContext,
    run: AssistantRunRecord,
    out_dir: Path,
) -> None:
    events: list[tuple[str, dict | None]] = []
    validation = _load_json_artifact(out_dir / VALIDATION_REPORT_PATH)
    if validation is not None:
        validation_status = str(validation.get("status", "unknown"))
        repository.create_validation_report(auth, run.id, status=validation_status, payload=validation)
        events.append(("validation.completed", {"status": validation_status}))

    review = _load_json_artifact(out_dir / REVIEW_REPORT_PATH)
    if review is not None:
        decision = str(review.get("decision") or review.get("status") or "completed")
        repository.create_review_report(auth, run.id, decision=decision, payload=review)
        events.append(("review.completed", {"decision": decision}))
    repository.append_run_events(auth, run.id, events)


def _load_json_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {"value": payload}
