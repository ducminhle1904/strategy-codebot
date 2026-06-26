#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="${STRATEGY_CODEBOT_PROD_SMOKE_PROJECT:-strategy-codebot-prod-live-smoke}"
BUILD=0
KEEP_ON_SUCCESS="${STRATEGY_CODEBOT_PROD_SMOKE_KEEP_ON_SUCCESS:-0}"
WORKERS=1
SYMBOL="BTC/USDT"
EXCHANGE="binance"
TIMEFRAME="1h"
CANDLE_TIMEFRAME="1m"
DAYS=365
REUSE_RANGE=0
WITH_LITELLM=0
AUTO_CHAIN=0
PROVIDER="${STRATEGY_CODEBOT_PROD_SMOKE_PROVIDER:-openrouter}"
MODEL="${STRATEGY_CODEBOT_PROD_SMOKE_MODEL:-openai/gpt-5.5}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  cat <<'USAGE'
Usage: scripts/prod-live-backtest-smoke.sh [options]

Runs an opt-in production-compose smoke: real model -> PineForge backtest -> CCXT BTC 1Y 1m execution.

Options:
  --build                    Build production images before running.
  --keep                     Keep the Docker stack after success.
  --workers N                backtest-worker replicas, default 1.
  --symbol SYMBOL            Default BTC/USDT.
  --exchange EXCHANGE        Default binance.
  --timeframe TIMEFRAME      Signal timeframe, default 1h.
  --candle-timeframe TF      Execution candle timeframe, default 1m.
  --days DAYS                Backtest range length, default 365.
  --provider PROVIDER        openrouter or vercel-ai-gateway, default openrouter.
  --model MODEL              Gateway model id, default openai/gpt-5.5.
  --reuse-range              Expect warm range-v2 cache reuse/performance.
  --auto-chain               Prove server-owned auto-chain queues the backtest after Pine generation.
  --with-litellm             Also start litellm-proxy and litellm-postgres.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      BUILD=1
      shift
      ;;
    --keep)
      KEEP_ON_SUCCESS=1
      shift
      ;;
    --workers)
      WORKERS="$2"
      shift 2
      ;;
    --symbol)
      SYMBOL="$2"
      shift 2
      ;;
    --exchange)
      EXCHANGE="$2"
      shift 2
      ;;
    --timeframe)
      TIMEFRAME="$2"
      shift 2
      ;;
    --candle-timeframe)
      CANDLE_TIMEFRAME="$2"
      shift 2
      ;;
    --days)
      DAYS="$2"
      shift 2
      ;;
    --provider)
      PROVIDER="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --reuse-range)
      REUSE_RANGE=1
      shift
      ;;
    --auto-chain)
      AUTO_CHAIN=1
      shift
      ;;
    --with-litellm)
      WITH_LITELLM=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

REPORT_DIR="$ROOT_DIR/reports/prod-live-smoke/$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$ROOT_DIR/compose.yml")
SERVICES=(postgres redis migration api pineforge-runner backtest-worker chat-worker)
if [[ "$WITH_LITELLM" == "1" ]]; then
  SERVICES=(postgres redis litellm-postgres litellm-proxy migration api pineforge-runner backtest-worker chat-worker)
fi

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-strategy-codebot-prod-smoke-postgres}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-strategy-codebot-prod-smoke-redis}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-litellm-prod-smoke-local}"
export LITELLM_SALT_KEY="${LITELLM_SALT_KEY:-strategy-codebot-prod-smoke-salt}"
export STRATEGY_CODEBOT_API_PORT="${STRATEGY_CODEBOT_PROD_SMOKE_API_PORT:-18080}"
export STRATEGY_CODEBOT_E2E_API_BASE_URL="${STRATEGY_CODEBOT_E2E_API_BASE_URL:-http://127.0.0.1:$STRATEGY_CODEBOT_API_PORT}"
export STRATEGY_CODEBOT_E2E_REPORT_DIR="$REPORT_DIR"
export STRATEGY_CODEBOT_RUN_PROD_LIVE_SMOKE=1
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_SYMBOL="$SYMBOL"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXCHANGE="$EXCHANGE"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEFRAME="$TIMEFRAME"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_CANDLE_TIMEFRAME="$CANDLE_TIMEFRAME"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_DAYS="$DAYS"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_EXPECT_WARM_CACHE="$REUSE_RANGE"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_AUTO_CHAIN="$AUTO_CHAIN"
export STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEOUT_SECONDS="${STRATEGY_CODEBOT_PROD_LIVE_SMOKE_TIMEOUT_SECONDS:-1200}"
export STRATEGY_CODEBOT_BACKTEST_AUTO_CHAIN_ENABLED=1
export BACKTEST_WORKER_MARKET_DATA_MODE=ccxt
export BACKTEST_ENGINE_DEFAULT=pineforge
export BACKTEST_PINEFORGE_ENABLED=1
export PINEFORGE_RUNNER_MODE="${PINEFORGE_RUNNER_MODE:-native}"
export STRATEGY_CODEBOT_LLM_MODE="${STRATEGY_CODEBOT_LLM_MODE:-live}"
export STRATEGY_CODEBOT_LLM_PROVIDER="$PROVIDER"
export STRATEGY_CODEBOT_LLM_MODEL="$MODEL"

if [[ "${STRATEGY_CODEBOT_LLM_MODE,,}" == "fake" ]]; then
  echo "STRATEGY_CODEBOT_LLM_MODE=fake is not allowed for production live smoke." >&2
  exit 2
fi
case "${PROVIDER,,}" in
  openrouter)
    export OPENROUTER_API_BASE="${OPENROUTER_API_BASE:-https://openrouter.ai/api/v1}"
    if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
      echo "OPENROUTER_API_KEY is required for --provider openrouter." >&2
      exit 2
    fi
    ;;
  vercel-ai-gateway|vercel_ai_gateway)
    export VERCEL_AI_GATEWAY_API_BASE="${VERCEL_AI_GATEWAY_API_BASE:-https://ai-gateway.vercel.sh/v1}"
    if [[ -z "${VERCEL_AI_GATEWAY_API_KEY:-}" ]]; then
      echo "VERCEL_AI_GATEWAY_API_KEY is required for --provider vercel-ai-gateway." >&2
      exit 2
    fi
    ;;
  *)
    echo "unsupported --provider: $PROVIDER (expected openrouter or vercel-ai-gateway)" >&2
    exit 2
    ;;
esac

mkdir -p "$REPORT_DIR"

wait_http() {
  local url="$1"
  local name="$2"
  "$PYTHON_BIN" - "$url" "$name" <<'PY'
import sys
import time
import urllib.request

url, name = sys.argv[1], sys.argv[2]
deadline = time.time() + 180
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if 200 <= response.status < 300:
                raise SystemExit(0)
    except Exception:
        pass
    time.sleep(2)
print(f"timed out waiting for {name} at {url}", file=sys.stderr)
raise SystemExit(1)
PY
}

collect_evidence() {
  local status="$1"
  "${COMPOSE[@]}" config > "$REPORT_DIR/compose-config.yml" 2>&1 || true
  "${COMPOSE[@]}" ps --all > "$REPORT_DIR/compose-ps.txt" 2>&1 || true
  "${COMPOSE[@]}" logs --no-color --timestamps --tail=800 > "$REPORT_DIR/compose-logs-tail.txt" 2>&1 || true
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}' > "$REPORT_DIR/docker-stats.txt" 2>&1 || true
  "$PYTHON_BIN" - "$REPORT_DIR" "$status" "$PROJECT_NAME" <<'PY'
import json
import pathlib
import sys

report_dir = pathlib.Path(sys.argv[1])
status = sys.argv[2]
project_name = sys.argv[3]
analysis = {
    "status": status,
    "project_name": project_name,
    "classification_hint": "If failed, inspect pytest-prod-live-smoke.log, prod-live-*.json, compose logs, readiness, and runner stats.",
    "evidence": sorted(path.name for path in report_dir.iterdir()),
}
(report_dir / "analysis.md").write_text(
    "# Production Live Backtest Smoke Analysis\n\n"
    f"- status: {status}\n"
    f"- docker project: `{project_name}`\n"
    "- likely categories: provider, public data, queue/worker, PineForge runner, artifact/API, performance threshold\n\n"
    "```json\n" + json.dumps(analysis, indent=2) + "\n```\n",
    encoding="utf-8",
)
PY
}

cleanup() {
  local exit_code=$?
  if [[ "$exit_code" -eq 0 ]]; then
    collect_evidence success
    if [[ "$KEEP_ON_SUCCESS" != "1" ]]; then
      "${COMPOSE[@]}" down --remove-orphans
    fi
    echo "Production live smoke passed. Evidence: $REPORT_DIR"
  else
    collect_evidence failure
    echo "Production live smoke failed. Stack kept for debugging." >&2
    local auto_chain_flag=""
    if [[ "$AUTO_CHAIN" == "1" ]]; then
      auto_chain_flag=" --auto-chain"
    fi
    echo "Repro: STRATEGY_CODEBOT_PROD_SMOKE_PROJECT=$PROJECT_NAME scripts/prod-live-backtest-smoke.sh --keep$auto_chain_flag --provider '$PROVIDER' --model '$MODEL' --symbol '$SYMBOL' --exchange '$EXCHANGE' --timeframe '$TIMEFRAME' --candle-timeframe '$CANDLE_TIMEFRAME' --days '$DAYS'" >&2
  fi
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ "$BUILD" == "1" ]]; then
  "${COMPOSE[@]}" build "${SERVICES[@]}"
fi

"${COMPOSE[@]}" up -d --build --scale "backtest-worker=$WORKERS" "${SERVICES[@]}"
wait_http "$STRATEGY_CODEBOT_E2E_API_BASE_URL/health" "api health"
wait_http "$STRATEGY_CODEBOT_E2E_API_BASE_URL/ready" "api readiness"
"${COMPOSE[@]}" exec -T pineforge-runner node -e "fetch('http://127.0.0.1:8080/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

PYTEST_TARGET="tests/e2e/docker/test_prod_live_backtest_smoke.py::test_prod_live_model_btc_1y_pineforge_backtest_smoke"
if [[ "$AUTO_CHAIN" == "1" ]]; then
  PYTEST_TARGET="tests/e2e/docker/test_prod_live_backtest_smoke.py::test_prod_live_model_auto_chain_btc_1y_pineforge_backtest_smoke"
fi

uv run pytest "$PYTEST_TARGET" -q \
  | tee "$REPORT_DIR/pytest-prod-live-smoke.log"
