# Backtest Kit Worker

Node/TypeScript worker boundary for `backtest-preview` jobs.

The Python API owns conversations, run records, auth, rate limits, and artifacts. This worker only leases `run_jobs` rows with `job_type = "backtest-preview"`, writes local preview artifacts under the shared artifact root, and appends typed run events.

Allowed runtime surface:

- `Backtest.run()` from `backtest-kit`.
- Read-only market data adapters.
- Local artifact writes under `STRATEGY_CODEBOT_API_ARTIFACT_ROOT`.

Blocked runtime surface:

- `Live.background()`.
- Broker credentials.
- Paper/live order execution.
- Telegram/live alert side effects.
- Docker live mode.

Local verification:

```bash
npm ci
npm run build
```

`node_modules/` and `dist/` are ignored workspace artifacts. Do not rely on checked-in dependencies when verifying the worker.

Load-hardening knobs:

- `BACKTEST_WORKER_LEASE_SECONDS` defaults to `120`.
- `BACKTEST_WORKER_HEARTBEAT_SECONDS` defaults to `30` and is capped at half the lease.
- `BACKTEST_WORKER_TIMEOUT_MS` defaults to `120000`.
- `BACKTEST_WORKER_MAX_CANDLES` defaults to `250000`.
- `BACKTEST_WORKER_MAX_ARTIFACT_BYTES` defaults to `5000000`.
- `BACKTEST_WORKER_DATA_FETCH_THROTTLE_MS` defaults to `250` per source/symbol/timeframe inside each worker process.
- `BACKTEST_WORKER_DEFAULT_WORKSPACE_ACTIVE_LIMIT` defaults to `2` for old queued jobs without payload limits.
- `BACKTEST_WORKER_CACHE_LOCK_TIMEOUT_MS` defaults to `30000` for shared-volume candle cache fills.

Artifact and cache keys are object-storage-compatible. Run artifacts use `runs/<run_id>/...`; reusable candle cache files use `cache/candles/<sha256>.json`.

Ecosystem packages:

- `@backtest-kit/pinets` is allowed for PineTS local preview artifacts. Label outputs as PineTS preview, not TradingView validation.
- `@backtest-kit/signals` is allowed for LLM-ready market context artifacts. Model routing remains in strategy-codebot.
- `@backtest-kit/graph` is allowed for multi-timeframe/variant composition artifacts.
- `@backtest-kit/sidekick` is export/scaffold-only via `npx`; it is not a worker/API runtime dependency.
- `@backtest-kit/ollama` is intentionally excluded because model routing belongs to strategy-codebot.
