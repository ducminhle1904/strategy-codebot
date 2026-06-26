import json
from collections.abc import Iterable

from strategy_codebot.server.ids import opaque_id
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import RunEventRecord

SSE_EVENT_TYPES = {
    "backtest.queued",
    "backtest.data.started",
    "backtest.data.planning",
    "backtest.data.cache_reusing",
    "backtest.data.fetching",
    "backtest.data.exporting",
    "backtest.data.completed",
    "backtest.execution.started",
    "backtest.execution.completed",
    "backtest.indexing.started",
    "backtest.report.completed",
    "backtest.failed",
    "backtest.preview.heartbeat",
    "message.delta",
    "provider.started",
    "provider.route",
    "provider.waiting",
    "provider.retrying",
    "model.reasoning.delta",
    "model.usage",
    "tool.started",
    "tool.completed",
    "validation.completed",
    "review.completed",
    "artifact.created",
    "policy.blocked",
    "observability.stage.completed",
    "progress.snapshot",
    "progress.update",
    "stage.started",
    "stage.completed",
    "run.completed",
    "run.failed",
    "run.cancelled",
}

DETERMINISTIC_DELTA_CHUNKS = (
    "## Review-only response\n\n",
    "I received the trading request and prepared a deterministic review placeholder.\n\n",
    "- Strategy context was accepted for review.\n",
    "- Static validation and review placeholders completed.\n",
    "- No live trading, broker execution, or platform runtime proof was performed.",
)


def sse_frame(event: RunEventRecord) -> str:
    data = {
        "event_id": event.id,
        "conversation_id": event.conversation_id,
        "run_id": event.run_id,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "sequence": event.sequence,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
    encoded = json.dumps(data, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {encoded}\n\n"


def compact_delta_text(chunks: Iterable[str] = DETERMINISTIC_DELTA_CHUNKS) -> str:
    return "".join(chunks)


def transient_delta_event(
    run: AssistantRunRecord,
    *,
    delta: str,
    chunk_index: int,
) -> RunEventRecord:
    return RunEventRecord(
        id=f"evt_transient_{opaque_id('evt').removeprefix('evt_')}",
        run_id=run.id,
        conversation_id=run.conversation_id,
        owner_user_id=run.owner_user_id,
        workspace_id=run.workspace_id,
        sequence=0,
        type="message.delta",
        payload={
            "delta": delta,
            "transient": True,
            "chunk_index": chunk_index,
            "request_id": run.request_id,
            "trace_id": run.trace_id,
            "conversation_id": run.conversation_id,
            "run_id": run.id,
        },
        created_at=run.created_at,
        request_id=run.request_id,
        trace_id=run.trace_id,
    )


def transient_reasoning_event(
    run: AssistantRunRecord,
    *,
    payload: dict,
) -> RunEventRecord:
    return RunEventRecord(
        id=f"evt_transient_{opaque_id('evt').removeprefix('evt_')}",
        run_id=run.id,
        conversation_id=run.conversation_id,
        owner_user_id=run.owner_user_id,
        workspace_id=run.workspace_id,
        sequence=0,
        type="model.reasoning.delta",
        payload={
            **payload,
            "transient": True,
        },
        created_at=run.created_at,
        request_id=run.request_id,
        trace_id=run.trace_id,
    )
