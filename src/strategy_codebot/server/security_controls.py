import hashlib
import json
from dataclasses import dataclass
from typing import Any

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.policy import PolicyFinding


class SecurityControlError(Exception):
    status_code = 503
    code = "security_controls_unavailable"
    dimension = "security_controls"
    retry_after_seconds = 30


class RateLimitExceeded(SecurityControlError):
    status_code = 429
    code = "rate_limit_exceeded"

    def __init__(self, dimension: str, retry_after_seconds: int) -> None:
        self.dimension = dimension
        self.retry_after_seconds = retry_after_seconds
        super().__init__(dimension)


class IdempotencyConflict(SecurityControlError):
    status_code = 409
    code = "idempotency_conflict"
    dimension = "idempotency"
    retry_after_seconds = 0


class IdempotencyInFlight(SecurityControlError):
    status_code = 409
    code = "idempotency_in_flight"
    dimension = "idempotency"
    retry_after_seconds = 1


class BudgetExceeded(SecurityControlError):
    status_code = 429
    code = "budget_exceeded"

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(dimension)


@dataclass(frozen=True)
class RateLimitRule:
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitConfig:
    user_minute: RateLimitRule = RateLimitRule(120, 60)
    user_day: RateLimitRule = RateLimitRule(2000, 86400)
    workspace_minute: RateLimitRule = RateLimitRule(600, 60)
    workspace_day: RateLimitRule = RateLimitRule(10000, 86400)
    ip_minute: RateLimitRule = RateLimitRule(180, 60)
    model_user_minute: RateLimitRule = RateLimitRule(60, 60)
    model_workspace_minute: RateLimitRule = RateLimitRule(300, 60)
    tool_user_minute: RateLimitRule = RateLimitRule(120, 60)
    tool_workspace_minute: RateLimitRule = RateLimitRule(600, 60)


@dataclass(frozen=True)
class RunBudgetConfig:
    max_total_tokens: int = 64000
    max_output_tokens: int = 16000
    max_tool_calls: int = 12
    max_runtime_seconds: int = 180
    max_artifacts: int = 12


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    response: dict[str, Any] | None = None
    status_code: int | None = None

    @property
    def replay(self) -> bool:
        return self.response is not None and self.status_code is not None


class SecurityControls:
    enabled = False
    budget_config = RunBudgetConfig()

    def check_write(self, auth: AuthContext, *, ip_address: str, surface: str) -> None:
        return None

    def check_model_call(self, auth: AuthContext, *, model: str) -> None:
        return None

    def check_tool_call(self, auth: AuthContext, *, tool_id: str) -> None:
        return None

    def check_run_start(self) -> None:
        return None

    def check_artifact_budget(self, current_count: int) -> None:
        return None

    def check_usage_budget(self, *, total_tokens: int, output_tokens: int) -> None:
        return None

    def begin_idempotency(
        self,
        auth: AuthContext,
        *,
        method: str,
        path: str,
        key: str | None,
        body: Any,
    ) -> IdempotencyRecord | None:
        return None

    def complete_idempotency(self, record: IdempotencyRecord | None, *, status_code: int, response: dict[str, Any]) -> None:
        return None


class RedisSecurityControls(SecurityControls):
    enabled = True

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: Any | None = None,
        rate_limit_config: RateLimitConfig | None = None,
        budget_config: RunBudgetConfig | None = None,
        key_prefix: str = "strategy-codebot-api",
    ) -> None:
        if redis_client is None:
            if redis_url is None:
                raise ValueError("redis_url or redis_client is required")
            try:
                from redis import Redis
            except Exception as exc:  # pragma: no cover - dependency import guard
                raise SecurityControlError("Redis dependency unavailable") from exc
            redis_client = Redis.from_url(redis_url, decode_responses=True)
        self.redis = redis_client
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.budget_config = budget_config or RunBudgetConfig()
        self.key_prefix = key_prefix

    def check_write(self, auth: AuthContext, *, ip_address: str, surface: str) -> None:
        self._ensure_available()
        self._check_limit("user", auth.user_id, self.rate_limit_config.user_minute)
        self._check_limit("user-day", auth.user_id, self.rate_limit_config.user_day)
        self._check_limit("workspace", auth.workspace_id, self.rate_limit_config.workspace_minute)
        self._check_limit("workspace-day", auth.workspace_id, self.rate_limit_config.workspace_day)
        self._check_limit("ip", ip_address or "unknown", self.rate_limit_config.ip_minute)

    def check_model_call(self, auth: AuthContext, *, model: str) -> None:
        self._ensure_available()
        self._check_limit("model-user", f"{auth.user_id}:{model}", self.rate_limit_config.model_user_minute)
        self._check_limit(
            "model-workspace",
            f"{auth.workspace_id}:{model}",
            self.rate_limit_config.model_workspace_minute,
        )

    def check_tool_call(self, auth: AuthContext, *, tool_id: str) -> None:
        self._ensure_available()
        self._check_limit("tool-user", f"{auth.user_id}:{tool_id}", self.rate_limit_config.tool_user_minute)
        self._check_limit("tool-workspace", f"{auth.workspace_id}:{tool_id}", self.rate_limit_config.tool_workspace_minute)

    def check_run_start(self) -> None:
        self._ensure_available()
        if self.budget_config.max_runtime_seconds <= 0:
            raise BudgetExceeded("runtime")
        if self.budget_config.max_artifacts <= 0:
            raise BudgetExceeded("artifacts")

    def check_artifact_budget(self, current_count: int) -> None:
        self._ensure_available()
        if current_count >= self.budget_config.max_artifacts:
            raise BudgetExceeded("artifacts")

    def check_usage_budget(self, *, total_tokens: int, output_tokens: int) -> None:
        self._ensure_available()
        if total_tokens > self.budget_config.max_total_tokens:
            raise BudgetExceeded("tokens")
        if output_tokens > self.budget_config.max_output_tokens:
            raise BudgetExceeded("output_tokens")

    def begin_idempotency(
        self,
        auth: AuthContext,
        *,
        method: str,
        path: str,
        key: str | None,
        body: Any,
    ) -> IdempotencyRecord | None:
        if not key:
            return None
        self._ensure_available()
        idem_key = self._idempotency_key(auth, method, path, key)
        body_hash = _body_hash(body)
        current = self.redis.get(idem_key)
        if current:
            payload = json.loads(current)
            if payload.get("body_hash") != body_hash:
                raise IdempotencyConflict("idempotency key reused with a different body")
            if payload.get("status") == "completed":
                return IdempotencyRecord(
                    key=idem_key,
                    response=payload.get("response"),
                    status_code=int(payload.get("status_code", 200)),
                )
            raise IdempotencyInFlight("idempotent request is still in flight")
        pending = {"status": "pending", "body_hash": body_hash}
        if not self.redis.set(idem_key, json.dumps(pending, sort_keys=True), nx=True, ex=86400):
            raise IdempotencyInFlight("idempotent request is still in flight")
        return IdempotencyRecord(key=idem_key)

    def complete_idempotency(self, record: IdempotencyRecord | None, *, status_code: int, response: dict[str, Any]) -> None:
        if record is None or record.replay:
            return
        self._ensure_available()
        current = self.redis.get(record.key)
        body_hash = json.loads(current).get("body_hash") if current else None
        payload = {
            "status": "completed",
            "body_hash": body_hash,
            "status_code": status_code,
            "response": response,
        }
        self.redis.set(record.key, json.dumps(payload, sort_keys=True, default=str), ex=86400)

    def _ensure_available(self) -> None:
        try:
            self.redis.ping()
        except Exception as exc:
            raise SecurityControlError("Redis controls unavailable") from exc

    def _check_limit(self, dimension: str, identifier: str, rule: RateLimitRule) -> None:
        key = f"{self.key_prefix}:rl:{dimension}:{identifier}"
        count = int(self.redis.incr(key))
        if count == 1:
            self.redis.expire(key, rule.window_seconds)
        if count > rule.limit:
            raise RateLimitExceeded(dimension, rule.window_seconds)

    def _idempotency_key(self, auth: AuthContext, method: str, path: str, key: str) -> str:
        digest = hashlib.sha256(f"{auth.user_id}:{auth.workspace_id}:{method}:{path}:{key}".encode()).hexdigest()
        return f"{self.key_prefix}:idem:{digest}"


def security_error_payload(error: SecurityControlError) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "dimension": error.dimension,
            "retry_after_seconds": error.retry_after_seconds,
        }
    }


def budget_policy_finding(error: BudgetExceeded) -> PolicyFinding:
    return PolicyFinding(
        severity="blocker",
        code="budget_exceeded",
        message=f"Run budget exceeded: {error.dimension}",
        surface=f"budget.{error.dimension}",
        evidence_level="strategy_idea",
    )


def _body_hash(body: Any) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
