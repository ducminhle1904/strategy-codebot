from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from statistics import quantiles
from typing import Any

from strategy_codebot.harness_types import FAILURE_PROVIDER_ERROR, FAILURE_PROVIDER_NOT_FOUND, FAILURE_PROVIDER_TIMEOUT, STATUS_FAIL, STATUS_PASS, STATUS_SKIPPED


ROUTE_HEALTH_DATABASE_URL_ENV = "STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL"
ROUTE_HEALTH_SLOW_STAGES = {"pine_code_generation", "balanced_review", "repair"}
ROUTE_HEALTH_SLOW_THRESHOLD_MS = 48_000
ROUTE_HEALTH_COOLDOWN_SECONDS = 600
ROUTE_HEALTH_MAX_RECENT_LATENCIES = 20


def configured_database_url(database_url: str | None = None) -> str | None:
    return database_url or os.environ.get(ROUTE_HEALTH_DATABASE_URL_ENV)


def load_route_health(*, database_url: str | None = None, user_tier: str | None = None, workflow: str | None = None) -> list[dict[str, Any]]:
    db_url = configured_database_url(database_url)
    if not db_url:
        return []
    try:
        filters: list[str] = []
        params: list[Any] = []
        if user_tier is not None:
            filters.append("user_tier = %s")
            params.append(user_tier)
        if workflow is not None:
            filters.append("workflow = %s")
            params.append(workflow)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        with _connect(db_url) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT *
                FROM harness_route_health
                {where_clause}
                ORDER BY user_tier, workflow, stage, route_model, gateway
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
    except Exception:
        return []


def route_health_report(*, database_url: str | None = None, user_tier: str | None = None, workflow: str | None = None) -> dict[str, Any]:
    db_url = configured_database_url(database_url)
    if not db_url:
        return {"status": "skipped", "store": "postgres", "configured": False, "routes": [], "route_count": 0}
    try:
        routes = load_route_health(database_url=db_url, user_tier=user_tier, workflow=workflow)
        return {
            "status": "pass",
            "store": "postgres",
            "configured": True,
            "route_count": len(routes),
            "routes": routes,
            "cooldown_count": sum(1 for route in routes if route.get("route_status") == "cooldown"),
            "unstable_count": sum(1 for route in routes if route.get("route_status") == "unstable"),
        }
    except Exception as exc:
        return {"status": "fail", "store": "postgres", "configured": True, "route_count": 0, "routes": [], "message": str(exc)}


def record_route_attempt(
    *,
    database_url: str | None = None,
    user_tier: str | None,
    workflow: str | None,
    attempt: dict[str, Any],
) -> None:
    db_url = configured_database_url(database_url)
    if not db_url or not _is_route_attempt(attempt):
        return
    status = str(attempt.get("status") or "")
    if status not in {STATUS_PASS, STATUS_FAIL, STATUS_SKIPPED}:
        return
    try:
        with _connect(db_url) as conn:
            _ensure_schema(conn)
            _upsert_attempt(conn, user_tier=user_tier, workflow=workflow, attempt=attempt)
    except Exception:
        return


def record_timeout_mirror_events(
    *,
    database_url: str | None = None,
    user_tier: str | None,
    workflow: str | None,
    events: list[dict[str, Any]],
) -> None:
    db_url = configured_database_url(database_url)
    started = [event for event in events if event.get("status") == "started" and _is_route_attempt(event)]
    if not db_url or not started:
        return
    last_started_by_route: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in started:
        key = (str(event.get("stage") or ""), str(event.get("route_model") or ""), str(event.get("gateway") or "litellm_proxy"))
        last_started_by_route[key] = event
    try:
        with _connect(db_url) as conn:
            _ensure_schema(conn)
            for event in last_started_by_route.values():
                timeout_attempt = {
                    **event,
                    "status": STATUS_FAIL,
                    "failure_class": FAILURE_PROVIDER_TIMEOUT,
                    "error_code": FAILURE_PROVIDER_TIMEOUT,
                    "timeout_overrun": True,
                }
                _upsert_attempt(conn, user_tier=user_tier, workflow=workflow, attempt=timeout_attempt)
    except Exception:
        return


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


def _ensure_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS harness_route_health (
            user_tier TEXT NOT NULL,
            workflow TEXT NOT NULL,
            stage TEXT NOT NULL,
            route_model TEXT NOT NULL,
            model TEXT NOT NULL,
            gateway TEXT NOT NULL,
            provider TEXT,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            timeout_count INTEGER NOT NULL DEFAULT 0,
            slow_count INTEGER NOT NULL DEFAULT 0,
            cooldown_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failure_count INTEGER NOT NULL DEFAULT 0,
            consecutive_failure_max INTEGER NOT NULL DEFAULT 0,
            last_latency_ms INTEGER,
            max_latency_ms INTEGER NOT NULL DEFAULT 0,
            recent_latency_ms JSONB NOT NULL DEFAULT '[]'::jsonb,
            last_failure_class TEXT,
            last_error TEXT,
            cooldown_until TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (user_tier, workflow, stage, route_model, gateway)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_harness_route_health_cooldown_until ON harness_route_health (cooldown_until)")


def _upsert_attempt(conn: Any, *, user_tier: str | None, workflow: str | None, attempt: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    now = datetime.now(UTC)
    stage = str(attempt.get("stage") or "single")
    route_model = _route_model(attempt)
    model = str(attempt.get("model") or (f"{attempt.get('gateway')}/{route_model}" if route_model else "unknown"))
    gateway = str(attempt.get("gateway") or ("litellm_proxy" if model.startswith("litellm_proxy/") else "direct"))
    provider = str(attempt.get("provider") or attempt.get("resolved_provider") or "unknown")
    status = str(attempt.get("status") or "")
    failure_class = str(attempt.get("failure_class") or "") or None
    latency_ms = _safe_int(attempt.get("stage_total_ms") or attempt.get("duration_ms") or attempt.get("latency_ms"))
    slow = _is_slow_attempt(stage, latency_ms)
    timeout = bool(attempt.get("timeout_overrun")) or failure_class == FAILURE_PROVIDER_TIMEOUT
    provider_failure = failure_class in {FAILURE_PROVIDER_ERROR, FAILURE_PROVIDER_TIMEOUT, FAILURE_PROVIDER_NOT_FOUND}
    success_inc = 1 if status == STATUS_PASS else 0
    failure_inc = 1 if status == STATUS_FAIL else 0
    timeout_inc = 1 if timeout else 0
    slow_inc = 1 if slow else 0
    row = conn.execute(
        """
        SELECT *
        FROM harness_route_health
        WHERE user_tier=%s AND workflow=%s AND stage=%s AND route_model=%s AND gateway=%s
        """,
        (user_tier or "unknown", workflow or "unknown", stage, route_model, gateway),
    ).fetchone()
    recent = list(row["recent_latency_ms"] or []) if row else []
    if latency_ms > 0:
        recent = [*recent, latency_ms][-ROUTE_HEALTH_MAX_RECENT_LATENCIES:]
    previous_consecutive = int(row["consecutive_failure_count"] or 0) if row else 0
    consecutive_failure_count = 0 if success_inc else previous_consecutive + 1 if provider_failure else previous_consecutive
    previous_max_consecutive = int(row["consecutive_failure_max"] or 0) if row else 0
    consecutive_failure_max = max(previous_max_consecutive, consecutive_failure_count)
    previous_timeout_count = int(row["timeout_count"] or 0) if row else 0
    previous_slow_count = int(row["slow_count"] or 0) if row else 0
    timeout_count = previous_timeout_count + timeout_inc
    slow_count = previous_slow_count + slow_inc
    should_cooldown = timeout_count >= 2 or slow_count >= 2 or consecutive_failure_count >= 2
    cooldown_until = datetime.fromtimestamp(time.time() + ROUTE_HEALTH_COOLDOWN_SECONDS, UTC) if should_cooldown else (row["cooldown_until"] if row else None)
    cooldown_inc = 1 if should_cooldown and not (row and row["cooldown_until"]) else 0
    conn.execute(
        """
        INSERT INTO harness_route_health (
            user_tier, workflow, stage, route_model, model, gateway, provider,
            success_count, failure_count, timeout_count, slow_count, cooldown_count,
            consecutive_failure_count, consecutive_failure_max, last_latency_ms, max_latency_ms,
            recent_latency_ms, last_failure_class, last_error, cooldown_until, updated_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_tier, workflow, stage, route_model, gateway) DO UPDATE SET
            model=EXCLUDED.model,
            provider=EXCLUDED.provider,
            success_count=harness_route_health.success_count + EXCLUDED.success_count,
            failure_count=harness_route_health.failure_count + EXCLUDED.failure_count,
            timeout_count=%s,
            slow_count=%s,
            cooldown_count=harness_route_health.cooldown_count + %s,
            consecutive_failure_count=%s,
            consecutive_failure_max=%s,
            last_latency_ms=EXCLUDED.last_latency_ms,
            max_latency_ms=GREATEST(harness_route_health.max_latency_ms, EXCLUDED.max_latency_ms),
            recent_latency_ms=EXCLUDED.recent_latency_ms,
            last_failure_class=EXCLUDED.last_failure_class,
            last_error=EXCLUDED.last_error,
            cooldown_until=EXCLUDED.cooldown_until,
            updated_at=EXCLUDED.updated_at
        """,
        (
            user_tier or "unknown",
            workflow or "unknown",
            stage,
            route_model,
            model,
            gateway,
            provider,
            success_inc,
            failure_inc,
            timeout_inc,
            slow_inc,
            cooldown_inc,
            consecutive_failure_count,
            consecutive_failure_max,
            latency_ms or None,
            latency_ms,
            Jsonb(recent),
            failure_class,
            str(attempt.get("error") or "") or None,
            cooldown_until,
            now,
            timeout_count,
            slow_count,
            cooldown_inc,
            consecutive_failure_count,
            consecutive_failure_max,
        ),
    )


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    recent = list(row.get("recent_latency_ms") or [])
    cooldown_until = row.get("cooldown_until")
    timeout_count = _safe_int(row.get("timeout_count"))
    slow_count = _safe_int(row.get("slow_count"))
    failure_count = _safe_int(row.get("failure_count"))
    route_status = "healthy"
    if cooldown_until and cooldown_until.timestamp() > time.time():
        route_status = "cooldown"
    elif timeout_count or slow_count >= 2:
        route_status = "unstable"
    elif failure_count:
        route_status = "degraded"
    return {
        **dict(row),
        "route_status": route_status,
        "status": route_status,
        "p95_latency_ms": _p95(recent),
        "recent_latency_ms": recent,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _is_route_attempt(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("route_model") or str(attempt.get("model") or "").startswith("litellm_proxy/"))


def _route_model(attempt: dict[str, Any]) -> str:
    route_model = attempt.get("route_model")
    if route_model:
        return str(route_model)
    model = str(attempt.get("model") or "")
    return model.split("/", 1)[1] if "/" in model else model or "unknown"


def _is_slow_attempt(stage: str, latency_ms: int) -> bool:
    return stage in ROUTE_HEALTH_SLOW_STAGES and latency_ms >= ROUTE_HEALTH_SLOW_THRESHOLD_MS


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    if len(values) < 2:
        return max(values)
    return int(quantiles(sorted(values), n=20, method="inclusive")[18])
