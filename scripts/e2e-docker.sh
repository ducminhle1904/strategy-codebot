#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${STRATEGY_CODEBOT_E2E_PROJECT:-strategy-codebot-e2e}"
WORKERS=4
ONLY="all"
JOBS=200
BUILD=0
LIVE_PROVIDER=0
PROFILE_ARGS=()
KEEP_ON_SUCCESS="${STRATEGY_CODEBOT_E2E_KEEP_ON_SUCCESS:-0}"
STARTED=0
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      BUILD=1
      shift
      ;;
    --workers)
      WORKERS="$2"
      shift 2
      ;;
    --only)
      ONLY="$2"
      shift 2
      ;;
    --jobs)
      JOBS="$2"
      shift 2
      ;;
    --profile)
      PROFILE_ARGS+=(--profile "$2")
      shift 2
      ;;
    --live-provider)
      LIVE_PROVIDER=1
      shift
      ;;
    --keep)
      KEEP_ON_SUCCESS=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

REPORT_DIR="$ROOT_DIR/reports/e2e/$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$ROOT_DIR/compose.yml" -f "$ROOT_DIR/compose.e2e.yml")
if [[ "${#PROFILE_ARGS[@]}" -gt 0 ]]; then
  COMPOSE+=("${PROFILE_ARGS[@]}")
fi

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-strategy-codebot-e2e-postgres}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-strategy-codebot-e2e-redis}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-litellm-e2e-local}"
export LITELLM_SALT_KEY="${LITELLM_SALT_KEY:-strategy-codebot-e2e-salt}"
export LITELLM_PROXY_API_KEY="${LITELLM_PROXY_API_KEY:-$LITELLM_MASTER_KEY}"
export STRATEGY_CODEBOT_E2E_API_PORT="${STRATEGY_CODEBOT_E2E_API_PORT:-18000}"
export STRATEGY_CODEBOT_E2E_WEB_PORT="${STRATEGY_CODEBOT_E2E_WEB_PORT:-13000}"
export STRATEGY_CODEBOT_API_PORT="$STRATEGY_CODEBOT_E2E_API_PORT"
export STRATEGY_CODEBOT_WEB_PORT="$STRATEGY_CODEBOT_E2E_WEB_PORT"
export STRATEGY_CODEBOT_E2E_API_BASE_URL="${STRATEGY_CODEBOT_E2E_API_BASE_URL:-http://127.0.0.1:$STRATEGY_CODEBOT_E2E_API_PORT}"
export STRATEGY_CODEBOT_E2E_WEB_BASE_URL="${STRATEGY_CODEBOT_E2E_WEB_BASE_URL:-http://127.0.0.1:$STRATEGY_CODEBOT_E2E_WEB_PORT}"
export STRATEGY_CODEBOT_E2E_POSTGRES_PORT="${STRATEGY_CODEBOT_E2E_POSTGRES_PORT:-55432}"
export STRATEGY_CODEBOT_E2E_REDIS_PORT="${STRATEGY_CODEBOT_E2E_REDIS_PORT:-56379}"
export STRATEGY_CODEBOT_E2E_POSTGRES_URL="${STRATEGY_CODEBOT_E2E_POSTGRES_URL:-postgresql://strategy_codebot:$POSTGRES_PASSWORD@127.0.0.1:$STRATEGY_CODEBOT_E2E_POSTGRES_PORT/strategy_codebot}"
export STRATEGY_CODEBOT_E2E_REDIS_URL="${STRATEGY_CODEBOT_E2E_REDIS_URL:-redis://:$REDIS_PASSWORD@127.0.0.1:$STRATEGY_CODEBOT_E2E_REDIS_PORT/0}"
export STRATEGY_CODEBOT_E2E_LOAD_JOBS="$JOBS"
export STRATEGY_CODEBOT_E2E_WORKERS="$WORKERS"
export STRATEGY_CODEBOT_E2E_REPORT_DIR="$REPORT_DIR"
export STRATEGY_CODEBOT_E2E_COMPOSE_PROJECT="$PROJECT_NAME"
export STRATEGY_CODEBOT_E2E_COMPOSE_FILES="$ROOT_DIR/compose.yml:$ROOT_DIR/compose.e2e.yml"
export STRATEGY_CODEBOT_E2E_LLM_MODE="${STRATEGY_CODEBOT_E2E_LLM_MODE:-fake}"
if [[ "$LIVE_PROVIDER" == "1" || "$ONLY" == "live-model-public-smoke" ]]; then
  export STRATEGY_CODEBOT_E2E_LLM_MODE="${STRATEGY_CODEBOT_E2E_LLM_MODE_LIVE:-live}"
fi
if [[ "$ONLY" == "public-data-smoke" || "$ONLY" == "live-model-public-smoke" ]]; then
  export BACKTEST_WORKER_MARKET_DATA_MODE=ccxt
fi
mkdir -p "$REPORT_DIR"

collect_evidence() {
  local status="$1"
  "${COMPOSE[@]}" config > "$REPORT_DIR/compose-config.yml" 2>&1 || true
  "${COMPOSE[@]}" ps --all > "$REPORT_DIR/compose-ps.txt" 2>&1 || true
  "${COMPOSE[@]}" logs --no-color --timestamps > "$REPORT_DIR/compose-logs.txt" 2>&1 || true
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}' > "$REPORT_DIR/docker-stats.txt" 2>&1 || true
  "$PYTHON_BIN" - "$REPORT_DIR" "$status" <<'PY'
import json
import pathlib
import sys

report_dir = pathlib.Path(sys.argv[1])
status = sys.argv[2]
analysis = {
    "status": status,
    "evidence": [
        "compose-config.yml",
        "compose-ps.txt",
        "compose-logs.txt",
        "docker-stats.txt",
        "pytest-api-worker.log",
        "pytest-chat-tools.log",
        "pytest-paper-bot.log",
        "pytest-paper-bot-native.log",
        "pytest-load.log",
        "web-e2e.log",
        "paper-bot-chat-plan.json",
        "paper-bot-redis-streams.json",
        "paper-bot-db-runtime.json",
        "audit.log",
    ],
    "classification_hint": "If failed, inspect failing test output, run IDs, queue snapshots, worker logs, and artifacts in this directory.",
}
(report_dir / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
(report_dir / "analysis.md").write_text(
    "# Docker E2E Analysis\n\n"
    f"Status: `{status}`\n\n"
    "Use `compose-logs.txt`, pytest logs, queue snapshots, and run event captures to classify failures as API contract, worker lease/cache, PineForge execution, public data dependency, web rendering, test harness, or infrastructure resource limit.\n",
    encoding="utf-8",
)
PY
}

cleanup() {
  local exit_code=$?
  if [[ "$STARTED" == "1" ]]; then
    if [[ "$exit_code" == "0" ]]; then
      collect_evidence "passed"
      if [[ "$KEEP_ON_SUCCESS" != "1" ]]; then
        "${COMPOSE[@]}" down --volumes --remove-orphans
      fi
    else
      collect_evidence "failed"
      echo "E2E failed. Evidence: $REPORT_DIR" >&2
      echo "Debug stack with: docker compose -p $PROJECT_NAME -f compose.yml -f compose.e2e.yml ps" >&2
    fi
  fi
}
trap cleanup EXIT

wait_url() {
  local url="$1"
  local name="$2"
  local deadline=$((SECONDS + 180))
  until "$PYTHON_BIN" - "$url" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        raise SystemExit(0 if 200 <= response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
  do
    if (( SECONDS > deadline )); then
      echo "timed out waiting for $name at $url" >&2
      return 1
    fi
    sleep 2
  done
}

run_pytest() {
  local label="$1"
  shift
  uv run pytest "$@" -q | tee "$REPORT_DIR/$label.log"
}

cd "$ROOT_DIR"

if [[ "$BUILD" == "1" ]]; then
  "${COMPOSE[@]}" build
fi

STARTED=1
SERVICES=(postgres redis litellm-proxy migration api backtest-worker chat-worker web)
if [[ "$ONLY" == "paper-bot" || "$ONLY" == "paper-bot-native" ]]; then
  SERVICES+=(market-data-collector nautilus-paper-worker)
fi
"${COMPOSE[@]}" up -d --build --scale "backtest-worker=$WORKERS" "${SERVICES[@]}"

wait_url "$STRATEGY_CODEBOT_E2E_API_BASE_URL/health" "api health"
wait_url "$STRATEGY_CODEBOT_E2E_API_BASE_URL/ready" "api readiness"
wait_url "$STRATEGY_CODEBOT_E2E_WEB_BASE_URL" "web"

case "$ONLY" in
  all)
    run_pytest pytest-api-worker tests/e2e/docker/test_api_backtest_worker.py
    run_pytest pytest-chat-tools tests/e2e/docker/test_chat_tools.py
    run_pytest pytest-load tests/e2e/docker/test_load.py
    ;;
  api-worker)
    run_pytest pytest-api-worker tests/e2e/docker/test_api_backtest_worker.py
    ;;
  chat-tools)
    run_pytest pytest-chat-tools tests/e2e/docker/test_chat_tools.py
    ;;
  paper-bot)
    run_pytest pytest-paper-bot tests/e2e/docker/test_paper_bot.py
    ;;
  paper-bot-native)
    run_pytest pytest-paper-bot-native tests/e2e/docker/test_paper_bot_native.py
    ;;
  load)
    run_pytest pytest-load tests/e2e/docker/test_load.py
    ;;
  web)
    if [[ -x "$ROOT_DIR/apps/web/node_modules/.bin/playwright" ]]; then
      (cd "$ROOT_DIR/apps/web" && npx playwright test e2e --reporter=line) | tee "$REPORT_DIR/web-e2e.log"
    else
      echo "Playwright is not installed in apps/web; skipping browser E2E." | tee "$REPORT_DIR/web-e2e.log"
    fi
    ;;
  public-data-smoke)
    run_pytest pytest-api-worker tests/e2e/docker/test_api_backtest_worker.py::test_backtest_preview_completes_with_real_worker
    ;;
  live-model-public-smoke)
    run_pytest pytest-chat-tools tests/e2e/docker/test_chat_tools.py::test_chat_tool_queues_backtest_preview_and_worker_completes_child_run
    ;;
  *)
    echo "unknown --only value: $ONLY" >&2
    exit 2
    ;;
esac

{
  echo "## npm audit worker"
  (cd "$ROOT_DIR/workers/backtest-worker" && npm audit --omit=dev) || echo "worker npm audit reported findings; see output above"
  echo "## npm audit web"
  (cd "$ROOT_DIR/apps/web" && npm audit --omit=dev) || echo "web npm audit reported findings; see output above"
} | tee "$REPORT_DIR/audit.log"

{
  echo "## runtime dependency guard"
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

worker = json.loads(Path("workers/backtest-worker/package.json").read_text())
deps = set(worker.get("dependencies", {}))
blocked_exact = {"backtest-" + "kit"}
blocked_prefix = "@backtest-" + "kit/"
found = sorted(dep for dep in deps if dep in blocked_exact or dep.startswith(blocked_prefix))
if found:
    raise SystemExit(f"blocked runtime dependency found: {found}")
print("blocked runtime dependencies absent")
PY
} | tee -a "$REPORT_DIR/audit.log"

if command -v strategy-codebot >/dev/null 2>&1; then
  strategy-codebot harness dev-trace --summary "Docker E2E PineForge integration run" --evidence "$REPORT_DIR/analysis.md" || true
  strategy-codebot harness audit-traces --latest 1 || true
fi
