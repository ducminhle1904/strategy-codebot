from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .helpers import auth
from .helpers import backtest_config
from .helpers import client
from .helpers import db_rows
from .helpers import percentile
from .helpers import valid_spec
from .helpers import wait_for_run_events
from .helpers import write_json


def test_backtest_queue_load_smoke_drains_without_duplicate_leases() -> None:
    total_jobs = int(os.getenv("STRATEGY_CODEBOT_E2E_LOAD_JOBS", "40"))
    total_jobs = max(1, total_jobs)
    workspace_count = min(10, max(1, total_jobs))
    headers_by_workspace = [auth(workspace=f"e2e-load-workspace-{index}") for index in range(workspace_count)]
    with client(timeout=60.0) as api:
        conversations = [
            api.post("/v1/conversations", headers=headers, json={"title": f"Load {index}"}).json()
            for index, headers in enumerate(headers_by_workspace)
        ]

    def enqueue(index: int) -> tuple[str, float, dict[str, str]]:
        headers = headers_by_workspace[index % workspace_count]
        conversation = conversations[index % workspace_count]
        started = time.perf_counter()
        with client(timeout=30.0) as api:
            response = api.post(
                "/v1/runs",
                headers=headers,
                json={
                    "conversation_id": conversation["id"],
                    "mode": "backtest-preview",
                    "strategy_spec": valid_spec(),
                    "backtest_config": backtest_config(start="2024-01-01T00:00:00Z", end="2024-01-01T06:00:00Z"),
                },
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["status"] == "queued"
            return payload["id"], elapsed_ms, headers

    run_headers: dict[str, dict[str, str]] = {}
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=min(25, total_jobs)) as executor:
        futures = [executor.submit(enqueue, index) for index in range(total_jobs)]
        for future in as_completed(futures):
            run_id, elapsed_ms, headers = future.result()
            run_headers[run_id] = headers
            latencies.append(elapsed_ms)

    p95_ms = percentile(latencies, 95)
    write_json("load-enqueue-latency.json", {"jobs": total_jobs, "p95_ms": p95_ms, "latencies_ms": latencies})
    assert p95_ms < 750, f"create-run p95 too slow: {p95_ms:.1f}ms"

    deadline = time.time() + 300
    with client(timeout=60.0) as api:
        for run_id, headers in run_headers.items():
            remaining = deadline - time.time()
            assert remaining > 0, "queue did not drain within 5 minutes"
            wait_for_run_events(api, headers, run_id, timeout_seconds=remaining)

    rows = db_rows(
        """
        SELECT status, count(*) AS count
        FROM run_jobs
        WHERE run_id = ANY(%s)
        GROUP BY status
        ORDER BY status
        """,
        (list(run_headers),),
    )
    write_json("load-run-jobs.json", rows)
    assert rows == [{"status": "completed", "count": total_jobs}]

    terminal_rows = db_rows(
        """
        SELECT run_id, count(*) AS terminal_events
        FROM run_events
        WHERE run_id = ANY(%s) AND type IN ('run.completed', 'run.failed', 'run.cancelled')
        GROUP BY run_id
        HAVING count(*) <> 1
        """,
        (list(run_headers),),
    )
    assert terminal_rows == []

    active_rows = db_rows(
        """
        SELECT count(*) AS stale_running
        FROM run_jobs
        WHERE run_id = ANY(%s) AND status = 'running'
        """,
        (list(run_headers),),
    )
    assert active_rows[0]["stale_running"] == 0

    cache_rows = db_rows(
        """
        SELECT count(*) AS manifest_count
        FROM artifacts
        WHERE run_id = ANY(%s) AND kind = 'market_data_cache_manifest'
        """,
        (list(run_headers),),
    )
    assert cache_rows[0]["manifest_count"] == total_jobs
