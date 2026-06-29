from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import os
import socket
import time
from typing import Any

from strategy_codebot.nautilus import nautilus_warmup_bar_count
from strategy_codebot.nautilus_streams import decode_stream_fields
from strategy_codebot.nautilus_streams import deterministic_event_key
from strategy_codebot.nautilus_streams import runtime_command_stream_key
from strategy_codebot.nautilus_streams import runtime_market_streams
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.database import create_sqlalchemy_repository
from strategy_codebot.server.nautilus_native_runner import NativeMarketMessage
from strategy_codebot.server.nautilus_native_runner import NativeNautilusExecutionRunner
from strategy_codebot.server.nautilus_native_runner import LOCAL_NAUTILUS_SOURCE
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.repository import NautilusRuntimeEventInput
from strategy_codebot.server.repository import NautilusRuntimeRecord


@dataclass(frozen=True)
class NautilusPaperWorkerConfig:
    worker_id: str
    database_url: str
    redis_url: str
    max_runtime_processes: int = 4
    max_strategies_per_runtime: int = 20
    heartbeat_interval_seconds: int = 15
    event_batch_size: int = 100
    stream_block_ms: int = 1000
    lease_seconds: int = 60
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "NautilusPaperWorkerConfig":
        return cls(
            worker_id=os.getenv("NAUTILUS_PAPER_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}",
            database_url=_required_env("STRATEGY_CODEBOT_API_DATABASE_URL"),
            redis_url=os.getenv("STRATEGY_CODEBOT_REDIS_URL")
            or f"redis://:{os.getenv('REDIS_PASSWORD', '')}@{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/0",
            max_runtime_processes=_positive_int_env("MAX_RUNTIME_PROCESSES", 4),
            max_strategies_per_runtime=_positive_int_env("MAX_STRATEGIES_PER_RUNTIME", 20),
            heartbeat_interval_seconds=_positive_int_env("HEARTBEAT_INTERVAL_SECONDS", 15),
            event_batch_size=_positive_int_env("EVENT_BATCH_SIZE", 100),
            stream_block_ms=_positive_int_env("STREAM_BLOCK_MS", 1000),
            lease_seconds=_positive_int_env("NAUTILUS_RUNTIME_LEASE_SECONDS", 60),
            poll_interval_seconds=_positive_float_env("NAUTILUS_WORKER_POLL_INTERVAL_SECONDS", 2.0),
        )


class NautilusPaperRuntimeRunner:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        redis_client: Any,
        worker_id: str,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: int = 15,
        event_batch_size: int = 100,
        stream_block_ms: int = 1000,
        native_runner: NativeNautilusExecutionRunner | None = None,
    ) -> None:
        self.repository = repository
        self.redis_client = redis_client
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.event_batch_size = event_batch_size
        self.stream_block_ms = stream_block_ms
        self.native_runner = native_runner or NativeNautilusExecutionRunner()

    def run_once(self, runtime: NautilusRuntimeRecord, *, already_renewed: bool = False) -> NautilusRuntimeRecord | None:
        auth = AuthContext(runtime.owner_user_id, runtime.workspace_id)
        if not already_renewed:
            renewed = self.repository.renew_nautilus_runtime_lease(
                runtime.id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if renewed is None:
                return None
            runtime = renewed
        command_events, command_cursors = self._consume_commands(runtime)
        if command_events:
            self.repository.append_nautilus_runtime_events_for_worker(
                runtime.id,
                worker_id=self.worker_id,
                events=command_events,
            )
            if any(event_type in {"stop_requested", "risk_block"} for event_type, _, _ in command_events):
                drop_runtime = getattr(self.native_runner, "drop_runtime", None)
                if callable(drop_runtime):
                    drop_runtime(runtime.id)
                return self.repository.release_nautilus_runtime_lease(
                    runtime.id,
                    worker_id=self.worker_id,
                    state="stopped",
                )
        if command_cursors:
            runtime = self.repository.persist_nautilus_runtime_stream_cursor(
                runtime.id,
                worker_id=self.worker_id,
                stream_cursor_json={**(runtime.stream_cursor_json or {}), **command_cursors},
            ) or runtime
        market_events, cursors, market_metrics = self._consume_market_data(runtime)
        if market_events:
            for event_batch in _event_batches(market_events, self.event_batch_size):
                self.repository.append_nautilus_runtime_events_for_worker(
                    runtime.id,
                    worker_id=self.worker_id,
                    events=event_batch,
                )
        if cursors:
            runtime = self.repository.persist_nautilus_runtime_stream_cursor(
                runtime.id,
                worker_id=self.worker_id,
                stream_cursor_json={**(runtime.stream_cursor_json or {}), **cursors},
            ) or runtime
        if not self._should_heartbeat(
            runtime,
            command_events=command_events,
            command_cursors=command_cursors,
            market_events=market_events,
            market_cursors=cursors,
        ):
            return runtime
        heartbeat = self.repository.record_nautilus_runtime_heartbeat(
            auth,
            runtime.id,
            payload={
                "status": "ok",
                "metrics": {
                    "worker_id": self.worker_id,
                    "paper_engine": LOCAL_NAUTILUS_SOURCE,
                    "stream_count": len(runtime_market_streams(runtime.data_subscriptions_json)),
                    "event_batch_size": len(market_events),
                    **market_metrics,
                },
            },
            idempotency_key=None,
        )
        return heartbeat.runtime if heartbeat is not None else runtime

    def _should_heartbeat(
        self,
        runtime: NautilusRuntimeRecord,
        *,
        command_events: list[NautilusRuntimeEventInput],
        command_cursors: dict[str, str],
        market_events: list[NautilusRuntimeEventInput],
        market_cursors: dict[str, str],
    ) -> bool:
        if runtime.last_heartbeat_at is None:
            return True
        if command_events or command_cursors or market_events or market_cursors:
            return True
        return datetime.now(UTC) - runtime.last_heartbeat_at >= timedelta(seconds=self.heartbeat_interval_seconds)

    def _consume_market_data(
        self,
        runtime: NautilusRuntimeRecord,
    ) -> tuple[list[NautilusRuntimeEventInput], dict[str, str], dict[str, Any]]:
        streams = runtime_market_streams(runtime.data_subscriptions_json)
        cursor_json = runtime.stream_cursor_json or {}
        streams = {stream: str(cursor_json.get(stream) or last_id) for stream, last_id in streams.items()}
        if not streams:
            return [], {}, {"paper_engine": LOCAL_NAUTILUS_SOURCE}
        messages = self._xread(streams, count=self.event_batch_size, block=self.stream_block_ms)
        cursors: dict[str, str] = {}
        native_messages: list[NativeMarketMessage] = []
        for stream, entries in messages:
            stream_name = _text(stream)
            for entry_id, fields in entries:
                redis_entry_id = _text(entry_id)
                payload = decode_stream_fields(fields)
                source_event_id = str(payload.get("event_id") or redis_entry_id)
                cursors[stream_name] = redis_entry_id
                native_messages.append(
                    NativeMarketMessage(
                        stream_name=stream_name,
                        stream_id=redis_entry_id,
                        source_event_id=source_event_id,
                        payload=payload,
                    )
                )
        warmup_probe = self.native_runner.run_bar_batch(runtime, [])
        if _needs_warmup_backfill(warmup_probe.metrics):
            native_messages = _merge_market_messages(
                self._warmup_backfill_messages(runtime, streams),
                native_messages,
            )
        if not native_messages:
            return warmup_probe.events, {}, warmup_probe.metrics
        result = self.native_runner.run_bar_batch(runtime, native_messages)
        if any(event_type == "runtime_error" for event_type, _, _ in result.events):
            cursors = {}
        return result.events, cursors, result.metrics

    def _warmup_backfill_messages(
        self,
        runtime: NautilusRuntimeRecord,
        streams: dict[str, str],
    ) -> list[NativeMarketMessage]:
        limit = _runtime_warmup_backfill_limit(runtime)
        if limit <= 0:
            return []
        messages: list[NativeMarketMessage] = []
        for stream, cursor in streams.items():
            if not stream.endswith(":bars"):
                continue
            max_id = cursor if cursor and cursor != "0-0" else "+"
            entries = list(reversed(self._xrevrange(stream, max_id=max_id, count=limit)))
            for entry_id, fields in entries:
                redis_entry_id = _text(entry_id)
                payload = decode_stream_fields(fields)
                if str(payload.get("closed")).lower() not in {"1", "true"}:
                    continue
                messages.append(
                    NativeMarketMessage(
                        stream_name=stream,
                        stream_id=redis_entry_id,
                        source_event_id=str(payload.get("event_id") or redis_entry_id),
                        payload=payload,
                    )
                )
        return messages[-limit:]

    def _consume_commands(self, runtime: NautilusRuntimeRecord) -> tuple[list[NautilusRuntimeEventInput], dict[str, str]]:
        stream = runtime_command_stream_key(runtime.id)
        cursor_json = runtime.stream_cursor_json or {}
        last_id = str(cursor_json.get(stream) or "0-0")
        messages = self._xread({stream: last_id}, count=10, block=None)
        events: list[NautilusRuntimeEventInput] = []
        cursor_updates: dict[str, str] = {}
        for _, entries in messages:
            for entry_id, fields in entries:
                source_event_id = _text(entry_id)
                payload = decode_stream_fields(fields)
                action = str(payload.get("action") or "")
                cursor_updates[stream] = source_event_id
                if action in {"stop", "reload"}:
                    events.append(
                        (
                            "stop_requested" if action == "stop" else "runtime_error",
                            {"source": "command_stream", **payload},
                            deterministic_event_key(
                                runtime_id=runtime.id,
                                source_event_id=source_event_id,
                                event_type=action,
                            ),
                        )
                    )
                elif action == "kill-switch":
                    events.append(
                        (
                            "risk_block",
                            {"source": "command_stream", **payload},
                            deterministic_event_key(
                                runtime_id=runtime.id,
                                source_event_id=source_event_id,
                                event_type="kill-switch",
                            ),
                        )
                    )
        return events, cursor_updates

    def _xread(
        self,
        streams: dict[str, str],
        *,
        count: int | None,
        block: int | None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        try:
            if block is None:
                return self.redis_client.xread(streams, count=count)
            return self.redis_client.xread(streams, count=count, block=block)
        except Exception:
            return []

    def _xrevrange(
        self,
        stream: str,
        *,
        max_id: str,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        try:
            return self.redis_client.xrevrange(stream, max=max_id, min="-", count=count)
        except Exception:
            pass
        try:
            entries = [
                (entry_id, fields)
                for entry_id, fields in self.redis_client.xrange(stream, min="-", max=max_id)
                if _stream_id_lte(_text(entry_id), max_id)
            ]
            return list(reversed(entries))[:count]
        except Exception:
            return []

class NautilusPaperWorker:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        redis_client: Any,
        config: NautilusPaperWorkerConfig,
    ) -> None:
        self.repository = repository
        self.redis_client = redis_client
        self.config = config
        self.runner = NautilusPaperRuntimeRunner(
            repository=repository,
            redis_client=redis_client,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
            heartbeat_interval_seconds=config.heartbeat_interval_seconds,
            event_batch_size=config.event_batch_size,
            stream_block_ms=config.stream_block_ms,
        )

    def run_once(self) -> int:
        stopped = 0
        stop_candidates = self.repository.list_desired_nautilus_runtimes(
            mode="paper",
            desired_state="stopping",
            worker_id=self.config.worker_id,
            limit=self.config.max_runtime_processes,
        )
        for runtime in stop_candidates:
            drop_runtime = getattr(self.runner.native_runner, "drop_runtime", None)
            if callable(drop_runtime):
                drop_runtime(runtime.id)
            released = self.repository.release_nautilus_runtime_lease(
                runtime.id,
                worker_id=self.config.worker_id,
                state="stopped",
            )
            if released is not None:
                stopped += 1

        candidates = self.repository.list_desired_nautilus_runtimes(
            mode="paper",
            desired_state="running",
            worker_id=self.config.worker_id,
            limit=self.config.max_runtime_processes,
        )
        claimed = 0
        for runtime in candidates:
            if len(runtime.strategy_ids) > self.config.max_strategies_per_runtime:
                continue
            lease = self.repository.claim_nautilus_runtime_lease(
                runtime.id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.lease_seconds,
            )
            if lease is None:
                continue
            claimed += 1
            self.runner.run_once(lease, already_renewed=True)
        return claimed + stopped

    def serve_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.config.poll_interval_seconds)


def create_redis_client(redis_url: str) -> Any:
    from redis import Redis

    return Redis.from_url(redis_url, decode_responses=True)


def main() -> None:
    config = NautilusPaperWorkerConfig.from_env()
    repository = create_sqlalchemy_repository(config.database_url)
    redis_client = create_redis_client(config.redis_url)
    NautilusPaperWorker(repository=repository, redis_client=redis_client, config=config).serve_forever()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = float(raw)
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _needs_warmup_backfill(metrics: dict[str, Any]) -> bool:
    return metrics.get("warmup_status") in {"pending", "warming_up"}


def _runtime_warmup_backfill_limit(runtime: NautilusRuntimeRecord) -> int:
    manifest = runtime.manifest_json if isinstance(runtime.manifest_json, dict) else {}
    strategy_spec = manifest.get("strategy_spec") if isinstance(manifest.get("strategy_spec"), dict) else {}
    paper_runtime = manifest.get("paper_runtime") if isinstance(manifest.get("paper_runtime"), dict) else {}
    override = paper_runtime.get("warmup_min_bars")
    try:
        required = nautilus_warmup_bar_count(strategy_spec, override=override if isinstance(override, int) else None)
    except Exception:
        return 0
    raw_warmup_bars = paper_runtime.get("warmup_bars") or manifest.get("warmup_bars") or []
    existing_warmup_count = len(raw_warmup_bars) if isinstance(raw_warmup_bars, list) else 0
    return max(0, required - existing_warmup_count)


def _merge_market_messages(
    backfill_messages: list[NativeMarketMessage],
    live_messages: list[NativeMarketMessage],
) -> list[NativeMarketMessage]:
    merged: list[NativeMarketMessage] = []
    seen: set[tuple[str, str]] = set()
    for message in [*backfill_messages, *live_messages]:
        identity = (message.stream_name, message.stream_id)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(message)
    return merged


def _stream_id_lte(left: str, right: str) -> bool:
    if right == "+":
        return True
    return _stream_id_tuple(left) <= _stream_id_tuple(right)


def _stream_id_tuple(value: str) -> tuple[int, int]:
    major, _, minor = value.partition("-")
    try:
        return int(major), int(minor or "0")
    except ValueError:
        return 0, 0


def _event_batches(
    events: list[NautilusRuntimeEventInput],
    batch_size: int,
) -> list[list[NautilusRuntimeEventInput]]:
    return [events[index : index + batch_size] for index in range(0, len(events), batch_size)]


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


if __name__ == "__main__":
    main()
