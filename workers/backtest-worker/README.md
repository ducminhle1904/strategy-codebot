# Backtest Worker

Node/TypeScript worker boundary for `backtest-preview` jobs.

The Python API owns conversations, run records, auth, rate limits, and artifact
metadata. This worker leases `run_jobs` rows with `job_type = "backtest-preview"`,
preloads read-only OHLCV data through the shared candle cache, calls the
dedicated `pineforge-runner`, writes local preview artifacts under the shared
artifact root, and appends typed run events.

Allowed runtime surface:

- `pineforge-runner` over the internal service URL.
- Read-only market data adapters.
- Local artifact writes under `STRATEGY_CODEBOT_API_ARTIFACT_ROOT`.

Blocked runtime surface:

- Broker credentials.
- Paper/live order execution.
- Telegram/webhook/live alert side effects.
- Docker live mode.

Local verification:

```bash
npm ci
npm run build
npm test
```

`node_modules/` and `dist/` are ignored workspace artifacts. Do not rely on
checked-in dependencies when verifying the worker.

Load-hardening knobs:

- `BACKTEST_WORKER_LEASE_SECONDS` defaults to `120`.
- `BACKTEST_WORKER_HEARTBEAT_SECONDS` defaults to `30` and is capped at half the lease.
- `BACKTEST_WORKER_TIMEOUT_MS` defaults to `600000`.
- `BACKTEST_WORKER_MAX_CANDLES` defaults to `1578240`.
- `BACKTEST_WORKER_MAX_ARTIFACT_BYTES` defaults to `200000000`.
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS` defaults to `250` per source/symbol/timeframe inside each worker process.
- `BACKTEST_WORKER_DATA_FETCH_CONCURRENCY` defaults to `2`; `BACKTEST_WORKER_GLOBAL_FETCH_ACTIVE_LIMIT` defaults to `2`.
- `BACKTEST_WORKER_DATA_FETCH_RETRY_ATTEMPTS` defaults to `3` with `BACKTEST_WORKER_DATA_FETCH_RETRY_BASE_MS=500`.
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_TTL_MS` defaults to `900000`, and `BACKTEST_WORKER_DATA_FETCH_THROTTLE_MAX_KEYS` defaults to `10000`.
- `BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT` defaults to `2` for old queued jobs without payload limits.
- `BACKTEST_WORKER_LONG_RANGE_ACTIVE_LIMIT` defaults to `1` per workspace for >1Y 1m jobs.
- `BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS` defaults to `30000` for shared-volume candle cache fills.
- `BACKTEST_WORKER_CACHE_SEGMENT_POLICY` defaults to `monthly`, with `BACKTEST_WORKER_CACHE_SEGMENT_TARGET_BYTES=60000000`.
- `BACKTEST_PINEFORGE_RUNNER_URL` defaults to `http://pineforge-runner:8080` in Compose.

Artifact and cache keys are object-storage-compatible. Run artifacts use
`runs/<run_id>/...`; reusable candle cache files use `cache/candles-v2/...`.
