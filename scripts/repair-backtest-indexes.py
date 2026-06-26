#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair artifact-backed backtest report indexes.")
    parser.add_argument("--run-id", action="append", default=[], help="Run id to repair. May be repeated.")
    parser.add_argument("--all-affected", action="store_true", help="Repair completed backtests with null indexed PnL.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect affected runs without writing.")
    args = parser.parse_args()

    run_ids = list(dict.fromkeys(args.run_id))
    with psycopg.connect(_database_url()) as conn:
      with conn.cursor() as cur:
        if args.all_affected:
            cur.execute(
                """
                select distinct r.run_id
                from backtest_reports r
                left join backtest_trade_index t on t.run_id = r.run_id
                where t.run_id is null or t.pnl_cost is null or t.pnl_percentage is null
                order by r.run_id
                """
            )
            run_ids.extend(row[0] for row in cur.fetchall())
        run_ids = list(dict.fromkeys(run_ids))
        if not run_ids:
            print("No runs selected.")
            return 0
        repaired = 0
        for run_id in run_ids:
            result = repair_run(conn, run_id, dry_run=args.dry_run)
            print(json.dumps(result, sort_keys=True))
            if result["status"] == "repaired":
                repaired += 1
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(json.dumps({"selected": len(run_ids), "repaired": repaired, "dry_run": args.dry_run}, sort_keys=True))
    return 0


def repair_run(conn: psycopg.Connection[Any], run_id: str, *, dry_run: bool) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select owner_user_id, workspace_id, metrics_json
            from backtest_reports
            where run_id = %s
            """,
            (run_id,),
        )
        report_row = cur.fetchone()
        if report_row is None:
            return {"run_id": run_id, "status": "missing_report"}
        owner_user_id, workspace_id, persisted_metrics = report_row
        cur.execute("select kind, storage_key from artifacts where run_id = %s", (run_id,))
        artifacts = {kind: storage_key for kind, storage_key in cur.fetchall()}

    root = Path(os.environ.get("STRATEGY_CODEBOT_API_ARTIFACT_ROOT") or os.environ.get("ARTIFACT_ROOT") or "/var/lib/strategy-codebot/artifacts")
    trades = _read_json(root, artifacts.get("backtest_trades"))
    equity = _read_json(root, artifacts.get("backtest_equity_curve"))
    report = _read_json(root, artifacts.get("backtest_report"))
    plan = _read_json(root, artifacts.get("backtest_plan"))
    if not isinstance(trades, list) or not isinstance(equity, list):
        return {"run_id": run_id, "status": "missing_artifacts"}

    config = plan.get("backtest_config", {}) if isinstance(plan, dict) else {}
    initial_capital = _number(config.get("initial_capital")) or 0
    normalized_trades = [_normalize_trade(trade, config) for trade in trades if isinstance(trade, dict)]
    normalized_equity = _normalize_equity(equity, initial_capital)
    metrics = dict(persisted_metrics or {})
    if isinstance(report, dict) and isinstance(report.get("metrics"), dict):
        metrics.update(report["metrics"])
    if normalized_equity:
        pnl_abs = normalized_equity[-1]["equity"] - initial_capital
        metrics["pnl"] = {
            "absolute": _round(pnl_abs),
            "percentage": _round(0 if initial_capital == 0 else (pnl_abs / initial_capital) * 100),
        }
        metrics["max_drawdown"] = _round(max(point["drawdown_pct"] for point in normalized_equity))
    metrics["trade_count"] = len(normalized_trades)
    quality = _quality_flags(config, normalized_trades, metrics)
    metrics["quality_flags"] = quality["flags"]
    metrics["quality_status"] = quality["status"]

    selected = _indexed_trades(normalized_trades)
    if dry_run:
        return {"run_id": run_id, "status": "would_repair", "trades": len(normalized_trades), "selected": len(selected), "quality": quality}

    with conn.cursor() as cur:
        cur.execute("update backtest_reports set metrics_json = %s::json where run_id = %s", (json.dumps(metrics), run_id))
        cur.execute("delete from backtest_trade_index where run_id = %s", (run_id,))
        for rank, item in enumerate(selected, 1):
            trade = item["trade"]
            cur.execute(
                """
                insert into backtest_trade_index (
                  id, run_id, owner_user_id, workspace_id, trade_rank, bucket, opened_at, closed_at,
                  pnl_cost, pnl_percentage, payload_json, created_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::json,now())
                """,
                (
                    f"btti_{uuid.uuid4().hex}",
                    run_id,
                    owner_user_id,
                    workspace_id,
                    rank,
                    item["bucket"],
                    trade.get("opened_at"),
                    trade.get("closed_at"),
                    trade.get("pnl_cost"),
                    trade.get("pnl_percentage"),
                    json.dumps(trade),
                ),
            )
        cur.execute(
            """
            update backtest_equity_summary
            set points_json = %s::json
            where run_id = %s
            """,
            (json.dumps(normalized_equity[:5000]), run_id),
        )
    return {"run_id": run_id, "status": "repaired", "trades": len(normalized_trades), "selected": len(selected), "quality": quality}


def _database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "strategy_codebot")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "strategy_codebot")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _read_json(root: Path, storage_key: str | None) -> Any:
    if not storage_key:
        return None
    with (root / storage_key).open(encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_trade(trade: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    entry = _number(trade.get("entry_price") or trade.get("entry"))
    qty = _number(trade.get("qty") or trade.get("quantity") or trade.get("contracts"))
    pnl = _number(trade.get("pnl_cost") or trade.get("pnl") or trade.get("profit") or trade.get("net_profit") or trade.get("net_pnl"))
    pnl_pct = _number(trade.get("pnl_percentage") or trade.get("pnl_pct") or trade.get("profit_percent") or trade.get("net_profit_percent"))
    cost = _number(trade.get("cost")) or (abs(qty * entry) if qty is not None and entry is not None else None)
    return {
        **trade,
        "opened_at": _isoish(trade.get("opened_at") or trade.get("entry_time") or trade.get("entry_time_ms")),
        "closed_at": _isoish(trade.get("closed_at") or trade.get("exit_time") or trade.get("exit_time_ms")),
        "entry_price": entry,
        "exit_price": _number(trade.get("exit_price") or trade.get("exit")),
        "raw_pnl_cost": pnl,
        "raw_pnl_percentage": pnl_pct,
        "pnl_cost": pnl,
        "pnl_percentage": pnl_pct,
        "cost": cost,
        "fee_cost": _number(trade.get("fee_cost") or trade.get("commission")) or 0,
        "slippage_cost": _number(trade.get("slippage_cost")) or 0,
        "cost_model": {
            "version": "fixed_bps_v1",
            "fee_bps": _number(config.get("fee_bps")) or 0,
            "slippage_bps": _number(config.get("slippage_bps")) or 0,
            "applied_to_metrics": True,
            "basis": "round_trip_notional",
        },
    }


def _normalize_equity(points: list[Any], initial_capital: float) -> list[dict[str, Any]]:
    previous = initial_capital
    peak = initial_capital
    normalized: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        equity = _number(point.get("equity") or point.get("value")) or previous
        peak = max(peak, equity)
        normalized.append({
            **point,
            "index": _number(point.get("index")) or index,
            "timestamp": _isoish(point.get("timestamp") or point.get("time") or point.get("time_ms")),
            "equity": equity,
            "pnl_cost": _round(equity - (initial_capital if index == 0 else previous)),
            "drawdown_pct": _round(0 if peak <= 0 else ((peak - equity) / peak) * 100),
        })
        previous = equity
    return normalized


def _indexed_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(enumerate(trades), key=lambda item: item[1].get("pnl_cost") or 0)
    selected: dict[int, dict[str, Any]] = {}
    for index, trade in ranked[:25]:
        selected[index] = {"bucket": "top_loser", "trade": trade}
    for index, trade in reversed(ranked[-25:]):
        selected[index] = {"bucket": "top_winner", "trade": trade}
    for index, trade in list(enumerate(trades))[:50]:
        selected.setdefault(index, {"bucket": "sample", "trade": trade})
    return [selected[index] for index in sorted(selected)]


def _quality_flags(config: dict[str, Any], trades: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    initial_capital = _number(config.get("initial_capital")) or 0
    max_notional = max([0, *[abs((trade.get("qty") or 0) * (trade.get("entry_price") or 0)) for trade in trades]])
    max_commission = max([0, *[trade.get("commission") or trade.get("fee_cost") or 0 for trade in trades]])
    flags: list[str] = []
    if initial_capital > 0 and max_notional / initial_capital > 20:
        flags.append("position_sizing_mismatch")
    elif initial_capital > 0 and max_notional / initial_capital > 5:
        flags.append("large_trade_notional")
    if initial_capital > 0 and max_commission / initial_capital > 1:
        flags.append("commission_exceeds_capital")
    pnl = metrics.get("pnl") if isinstance(metrics.get("pnl"), dict) else {}
    if (_number(pnl.get("percentage")) or 0) <= -100:
        flags.append("extreme_loss")
    status = "fail" if {"position_sizing_mismatch", "commission_exceeds_capital"} & set(flags) else ("warn" if flags else "pass")
    return {"status": status, "flags": sorted(set(flags))}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round(value: float) -> float:
    return round(value, 4)


def _isoish(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        milliseconds = value if abs(value) >= 1_000_000_000_000 else value * 1000
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
