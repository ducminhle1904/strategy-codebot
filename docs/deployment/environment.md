# Environment Configuration

Strategy Codebot keeps the default environment small. Set secrets and deployment
addresses explicitly; leave stable runtime behavior to code, compose, and the
model registry defaults.

## Required Secrets

| Area | Variables | Notes |
| --- | --- | --- |
| Local databases | `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | Required by Docker Compose. |
| Provider credentials | `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, optional provider keys | Only the selected model routes need credentials. |
| Proxy routing | `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `LITELLM_PROXY_API_KEY` | Required only for proxy-backed registry tiers. |
| Web/API boundary | `STRATEGY_CODEBOT_INTERNAL_AUTH_SECRET` | Required when the web proxy and API are not trusted local services. |
| Clerk auth | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Optional for local no-auth mode. |

## Defaulted Runtime Config

These values have code or compose defaults and usually do not belong in local
`.env` files:

| Area | Defaults |
| --- | --- |
| Model routing | `STRATEGY_CODEBOT_LLM_ROUTING=registry`, `STRATEGY_CODEBOT_MODEL_REGISTRY=configs/model-registry.example.yaml` |
| Classifier timeout | `STRATEGY_CODEBOT_CLASSIFIER_TIMEOUT_SECONDS=25` |
| Route timeout | `STRATEGY_CODEBOT_LLM_ROUTE_TIMEOUT_SECONDS=25` |
| Knowledge learning | `STRATEGY_CODEBOT_KNOWLEDGE_AUTO_CANDIDATES_ENABLED=1`; guarded auto-promotion is the default runtime behavior |
| Provider URLs | OpenRouter, OpenAI embeddings, Vercel AI Gateway, and Docker LiteLLM proxy base URLs |
| Market data | `MARKET_DATA_PROVIDER=auto`, CCXT exchange/timeframe/limit defaults |
| Web chat timeouts | first event `90000ms`, idle `60000ms`, total `180000ms` |
| Debug flags | off by default |

## Local, Docker, And Live Smoke Templates

- Use `.env.example` for direct local development with minimal secrets.
- Use `.env.docker.example` when running the full Docker Compose stack.
- Use `.env.live-smoke.example` only for opt-in live model tests.
- Use `STRATEGY_CODEBOT_SERVER_USER_TIER=dev` for local registry routing through
  direct OpenRouter routes.
- Use `STRATEGY_CODEBOT_SERVER_USER_TIER=paid_low` with the compose proxy stack.

## Advanced Worker Tuning

Local preview worker limits and fetch/cache tuning remain available through env
overrides but are intentionally omitted from the minimal template. Override them
only for load testing or incident mitigation:

- `BACKTEST_WORKER_TIMEOUT_MS`
- `BACKTEST_WORKER_MAX_CANDLES`
- `BACKTEST_WORKER_MAX_CANDLES_PER_FETCH`
- `BACKTEST_WORKER_MAX_EVENTS`
- `BACKTEST_WORKER_MAX_ARTIFACT_BYTES`
- `BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS`
- `BACKTEST_WORKER_CANDLE_CACHE_VERSION`
- `BACKTEST_WORKER_CACHE_SEGMENT_POLICY`
- `BACKTEST_WORKER_CACHE_SEGMENT_TARGET_BYTES`
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS`
- `BACKTEST_WORKER_DATA_FETCH_CONCURRENCY`
- `BACKTEST_WORKER_FETCH_PROGRESS_MIN_PERCENT_STEP`
- `BACKTEST_WORKER_FETCH_PROGRESS_MIN_INTERVAL_MS`
- `BACKTEST_WORKER_GLOBAL_FETCH_ACTIVE_LIMIT`
- `BACKTEST_WORKER_DATA_FETCH_RETRY_ATTEMPTS`
- `BACKTEST_WORKER_DATA_FETCH_RETRY_BASE_MS`
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_TTL_MS`
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_MAX_KEYS`
- `BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT`
- `BACKTEST_WORKER_LONG_RANGE_ACTIVE_LIMIT`
- `BACKTEST_WORKER_MARKET_DATA_MODE`
- `BACKTEST_WORKER_ALLOWED_EXCHANGES`
- `BACKTEST_WORKER_DEFAULT_EXCHANGE`

Keep user-facing product copy on local preview and preview compatibility. Use
internal service names only in developer config and operational docs.
