# Docker Deployment

This stack is backend-only: API, migration, Knowledge Base initialization,
Postgres with pgvector, Redis, and a private artifact volume. It does not add
frontend UI, broker/exchange integration, order execution, or billing.

## Files

- `Dockerfile` builds a multi-stage Python 3.13 image with `uv`, production
  dependencies, non-root runtime, and an API healthcheck.
- `compose.yml` defines the prod-like backend stack, including the pgvector
  database image and one-shot Knowledge Base initialization.
- `compose.dev.yml` only exposes Postgres and Redis ports for local debugging.
- `.dockerignore` excludes local state, secrets, harness data, virtualenvs, and
  generated artifacts from the build context.

## Environment

```bash
cp .env.docker.example .env
```

Edit `.env` before running Compose. At minimum set `POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, `LITELLM_MASTER_KEY`, and `LITELLM_SALT_KEY`. Production
deployments must replace every placeholder key. Provider keys such as
`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `VERCEL_AI_GATEWAY_API_KEY`,
`PORTKEY_API_KEY`, or direct provider keys can stay empty until you
intentionally run live provider generation.

The local `.env` file is ignored and must not be committed. `docker/secrets/`
also remains ignored to avoid accidentally committing old local secret files,
but the Compose stack no longer depends on `docker/secrets/*.txt`.

## Run

Build and start the backend stack:

```bash
docker compose -f compose.yml -f compose.dev.yml up --build
```

The `migration` service runs:

```bash
alembic -x database_url="$STRATEGY_CODEBOT_API_DATABASE_URL" upgrade head
```

The `knowledge-init` service then runs:

```bash
strategy-codebot knowledge init \
  --embedding-profile production-openrouter \
  --db-url "$STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL"
```

The API listens on `http://127.0.0.1:8000`.

The Compose stack also starts an internal LiteLLM Proxy service at
`http://litellm-proxy:4000/v1`; with `compose.dev.yml` it is exposed at
`http://127.0.0.1:4000`. Strategy Codebot routes `litellm_proxy/...` models to
that service using `LITELLM_PROXY_API_KEY`, which defaults to the same value as
`LITELLM_MASTER_KEY` in `.env.docker.example`. For production, create LiteLLM virtual
keys with the master key and set `LITELLM_PROXY_API_KEY` to a scoped virtual key
instead of sharing the master key with the app.

Paid user tiers route through LiteLLM aliases such as
`paid_medium.strategy_coding`; LiteLLM then selects the configured upstream
model candidates. The free tier continues to use direct OpenRouter free models
and does not depend on the proxy.

Treat paid aliases as stable application API. Backend candidates in
`docker/litellm/config.yaml` are tuned from measured latency, visible-content
behavior, and structured-output compatibility. Do not promote experimental
models such as Kimi/Qwen/MiniMax into high-traffic alias positions without
matrix evidence; use LiteLLM deployment weights and cooldowns to keep stable
Gemini/DeepSeek/OpenAI/Anthropic routes preferred.

Vercel AI Gateway can also be used behind LiteLLM Proxy through
`VERCEL_AI_GATEWAY_API_KEY` and `VERCEL_AI_GATEWAY_API_BASE`, and the Compose
service passes those values to the proxy. Vercel is enabled only as a
low-weight `paid_low`/`paid_medium` fallback for schema-simple stages
(`strategy_reasoning`, `pine_code_generation`, and `balanced_review`). Keep it
out of `strategy_coding` and `repair` aliases until forced Vercel multi-agent
runs pass, because Gemini rejects some OpenAI-style nullable nested schema
shapes. Use `response_format.type=json_schema`; `json_object` is not a valid
Vercel structured-output smoke for this stack. Direct app routes such as
`vercel_ai_gateway/google/gemini-2.5-flash-lite` remain useful for small smoke
tests while the local Vercel budget is limited, and Strategy Codebot sanitizes
those direct schemas to a Gemini-compatible subset. Compose also passes Portkey
and OpenRouter base-url envs to the proxy so future backend candidates can be
added without changing the app container contract.
The proxy exposes `diagnostics.vercel_gemini_flash_lite` as an operator-only
smoke alias for Vercel connectivity; keep production virtual keys scoped to the
paid tier aliases rather than this diagnostic alias.

Paid-tier live harness runs should execute inside the `api` container, or from a
host shell that has explicitly sourced `LITELLM_PROXY_API_BASE` and
`LITELLM_PROXY_API_KEY`. The live preflight fails fast with
`configuration_error` when a `litellm_proxy/...` route is selected without those
gateway env vars, so missing host env does not look like a provider connection
failure. Routine latency checks should use the container path, for example:

```bash
docker compose exec -T api strategy-codebot harness latency-matrix \
  --suite examples/evals/price-action-smoke.yaml \
  --runs 3 \
  --user-tier paid_low \
  --knowledge-context auto
```

LiteLLM Proxy owns the paid-tier provider control plane: upstream fallback,
provider cooldowns, virtual-key budgets, spend tracking, provider quirks, and
model rollout behind stable aliases. Strategy Codebot owns the trading workflow:
reasoning/coding/Pine/review/repair orchestration, Knowledge Base context,
policy gates, static validation, quality reports, and eval artifacts.

For production, create scoped LiteLLM virtual keys per tier/workspace and use
those keys as `LITELLM_PROXY_API_KEY`; keep `LITELLM_MASTER_KEY` for admin
setup only. A typical setup is:

```bash
strategy-codebot models litellm keys aliases --tier paid_medium
strategy-codebot models litellm keys provision \
  --tier paid_medium \
  --workspace-id workspace-id \
  --budget-duration 30d \
  --out /secure/path/litellm-paid-medium-key.json
```

The budget values in the LiteLLM provisioning docs are defaults for provisioning
virtual keys; LiteLLM virtual-key budgets are the source of truth for provider
spend enforcement. Strategy Codebot still keeps API/workspace rate limits for
application protection.

The generated JSON contains a secret key. Store it in your secret manager or
local `.env` as `LITELLM_PROXY_API_KEY`; do not commit it. Validate production
readiness with:

```bash
strategy-codebot models litellm keys check --production
```

`compose.yml` uses `pgvector/pgvector:pg17`. If an older local
`postgres-data` volume was created from plain `postgres:17-alpine`, recreate the
volume before enabling the Knowledge Base so `CREATE EXTENSION vector` is
available.

## Health

Use shallow liveness for container health:

```bash
curl http://127.0.0.1:8000/health
```

Use readiness for dependency checks:

```bash
curl http://127.0.0.1:8000/ready
```

`/ready` checks repository, artifact storage, Redis-backed controls when
configured, LLM provider configuration, Knowledge Base readiness, and the
current run worker. After successful Knowledge Base initialization, the
`knowledge_base` check should include:

```json
{
  "status": "ok",
  "configured": true,
  "embedding_provider": "openrouter",
  "embedding_model": "openai/text-embedding-3-small",
  "embedding_dimension": 1536
}
```

## E2E With Real Containers

After Compose is running, the optional live-service tests can target real
Postgres and Redis:

```bash
export STRATEGY_CODEBOT_API_E2E_POSTGRES_URL='postgresql+psycopg://strategy_codebot:<postgres-password>@127.0.0.1:5432/strategy_codebot'
export STRATEGY_CODEBOT_API_E2E_REDIS_URL='redis://:<redis-password>@127.0.0.1:6379/0'
uv run pytest tests/test_server_e2e.py -q
```

Provider calls should remain explicit. Do not run real LLM/live-generation
smokes without intentionally supplying provider credentials and accepting cost.

## PineForge Native Runner

Production `compose.yml` builds `pineforge-runner` as a self-contained native
image. The image builds `pineforge-engine` from `PINEFORGE_ENGINE_REF` (default
`v0.10.13`), installs `pineforge-codegen` from `PINEFORGE_CODEGEN_VERSION`
(default `0.8.0`), and exposes readiness fields for `engine_version`,
`codegen_version`, and `native_ready`.

Do not pass a host `PINEFORGE_NATIVE_COMMAND` in production. The worker talks to
`http://pineforge-runner:8080`; the runner compiles/runs Pine locally inside its
own container and writes bounded artifacts to the shared artifact volume.

Backtest OHLCV remains public read-only data fetched by `backtest-worker`, not by
PineForge. Production supports `BACKTEST_WORKER_ALLOWED_EXCHANGES`
(default `binance,bybit,okx,kraken`) and `BACKTEST_WORKER_DEFAULT_EXCHANGE`
(default `binance`). The run `backtest_config.exchange` selects the data venue
for reproducibility; it is not a broker or live-execution setting.

## PineForge Docker E2E

Run the full real-service PineForge E2E stack with:

```bash
scripts/e2e-docker.sh --build --workers 4
```

The runner starts Docker Compose with `compose.yml` plus `compose.e2e.yml`,
including Postgres, Redis, migration, API, web, LiteLLM Proxy, and one or more
`backtest-worker` replicas. The default E2E profile sets
`STRATEGY_CODEBOT_LLM_MODE=fake` and `BACKTEST_WORKER_MARKET_DATA_MODE=fixture`
so the tests are deterministic and do not spend provider budget or depend on a
public OHLCV API.
To avoid clashing with an existing local dev stack, the runner maps API/web to
`127.0.0.1:18000` and `127.0.0.1:13000` by default.

Useful focused runs:

```bash
scripts/e2e-docker.sh --only api-worker --workers 4
scripts/e2e-docker.sh --only chat-tools --workers 4
scripts/e2e-docker.sh --only load --workers 4 --jobs 200
scripts/e2e-docker.sh --only web
scripts/e2e-docker.sh --only public-data-smoke
```

Each run writes evidence to `reports/e2e/<timestamp>/`, including compose
config, service status, logs, Docker stats, pytest logs, audit output, and an
`analysis.md` file. On failure the stack is left running for inspection; on
success it is torn down unless `--keep` is passed.

The optional `public-data-smoke` target flips the worker to CCXT/public data for
one narrow smoke. Keep it serial and explicit because public data providers can
rate limit or fail independently of Strategy Codebot.

## Production Live Backtest Smoke

Use the production-only smoke when you intentionally want the full live path:
real model generation, production `compose.yml`, CCXT public OHLCV, native
`pineforge-runner`, and BTC 1Y with `1m` execution candles.

```bash
OPENROUTER_API_KEY=... \
STRATEGY_CODEBOT_RUN_PROD_LIVE_SMOKE=1 \
scripts/prod-live-backtest-smoke.sh --build --provider openrouter --symbol BTC/USDT --exchange binance --timeframe 1h --candle-timeframe 1m --days 365
```

For Vercel AI Gateway:

```bash
VERCEL_AI_GATEWAY_API_KEY=... \
STRATEGY_CODEBOT_RUN_PROD_LIVE_SMOKE=1 \
scripts/prod-live-backtest-smoke.sh --build --provider vercel-ai-gateway --symbol BTC/USDT --exchange binance --timeframe 1h --candle-timeframe 1m --days 365
```

The script refuses `STRATEGY_CODEBOT_LLM_MODE=fake`, sets the selected gateway
through `STRATEGY_CODEBOT_LLM_PROVIDER`/`STRATEGY_CODEBOT_LLM_MODEL`, sets
`BACKTEST_WORKER_MARKET_DATA_MODE=ccxt`, and writes evidence to
`reports/prod-live-smoke/<timestamp>/`. For warm-cache performance confirmation,
rerun the same range with `--reuse-range`; the pytest assertion expects range-v2
cache reuse and a fast worker path.
