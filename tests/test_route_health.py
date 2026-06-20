from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from strategy_codebot import route_health


class _FakeRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, list[Any]]] = []

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> _FakeRows:
        self.calls.append((sql, list(params or [])))
        return _FakeRows(self.rows)


def test_load_route_health_uses_typed_filters(monkeypatch) -> None:
    row = {
        "user_tier": "paid_low",
        "workflow": "multi-agent",
        "stage": "repair",
        "route_model": "paid_low.repair",
        "model": "litellm_proxy/paid_low.repair",
        "gateway": "litellm_proxy",
        "provider": "unknown",
        "success_count": 0,
        "failure_count": 2,
        "timeout_count": 2,
        "slow_count": 2,
        "cooldown_count": 1,
        "consecutive_failure_count": 2,
        "consecutive_failure_max": 2,
        "last_latency_ms": 90_000,
        "max_latency_ms": 91_000,
        "recent_latency_ms": [90_000, 91_000],
        "last_failure_class": "provider_timeout",
        "last_error": None,
        "cooldown_until": datetime(2099, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 18, tzinfo=UTC),
    }
    fake = _FakeConnection([row])
    monkeypatch.setattr(route_health, "_connect", lambda _database_url: fake)
    monkeypatch.setattr(route_health, "_ensure_schema", lambda _conn: None)

    routes = route_health.load_route_health(database_url="postgresql://example", user_tier="paid_low", workflow="multi-agent")

    assert routes[0]["route_status"] == "cooldown"
    query, params = fake.calls[0]
    assert "%s IS NULL" not in query
    assert "user_tier = %s" in query
    assert "workflow = %s" in query
    assert params == ["paid_low", "multi-agent"]
