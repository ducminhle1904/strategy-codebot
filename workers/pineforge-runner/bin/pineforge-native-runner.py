#!/usr/bin/env python3
"""Native PineForge runner command used by the Node HTTP service.

The command is intentionally stdin/stdout based so the HTTP service can keep a
small, stable process contract while the native compile/run implementation
evolves with PineForge's C ABI.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


RUNNER_VERSION = os.environ.get("PINEFORGE_RUNNER_VERSION", "pineforge-runner-native-contract-v1")
ENGINE_VERSION = os.environ.get("PINEFORGE_ENGINE_VERSION", "unknown")
CODEGEN_VERSION = os.environ.get("PINEFORGE_CODEGEN_VERSION", "unknown")
PINEFORGE_PREFIX = Path(os.environ.get("PINEFORGE_PREFIX", "/opt/pineforge"))
PINEFORGE_INCLUDE_DIR = Path(os.environ.get("PINEFORGE_INCLUDE_DIR", str(PINEFORGE_PREFIX / "include")))
PINEFORGE_LIB_DIR = Path(os.environ.get("PINEFORGE_LIB_DIR", str(PINEFORGE_PREFIX / "lib")))
CXX = os.environ.get("PINEFORGE_CXX", "c++")


class BarC(ctypes.Structure):
    _fields_ = [
        ("open", ctypes.c_double),
        ("high", ctypes.c_double),
        ("low", ctypes.c_double),
        ("close", ctypes.c_double),
        ("volume", ctypes.c_double),
        ("timestamp", ctypes.c_int64),
    ]


class TradeC(ctypes.Structure):
    _fields_ = [
        ("entry_time", ctypes.c_int64),
        ("exit_time", ctypes.c_int64),
        ("entry_price", ctypes.c_double),
        ("exit_price", ctypes.c_double),
        ("pnl", ctypes.c_double),
        ("pnl_pct", ctypes.c_double),
        ("is_long", ctypes.c_int),
        ("max_runup", ctypes.c_double),
        ("max_drawdown", ctypes.c_double),
        ("qty", ctypes.c_double),
        ("commission", ctypes.c_double),
        ("entry_bar_index", ctypes.c_int32),
        ("exit_bar_index", ctypes.c_int32),
    ]


class TradeStatsC(ctypes.Structure):
    _fields_ = [
        ("num_trades", ctypes.c_int32),
        ("num_wins", ctypes.c_int32),
        ("num_losses", ctypes.c_int32),
        ("num_even", ctypes.c_int32),
        ("percent_profitable", ctypes.c_double),
        ("net_profit", ctypes.c_double),
        ("net_profit_pct", ctypes.c_double),
        ("gross_profit", ctypes.c_double),
        ("gross_profit_pct", ctypes.c_double),
        ("gross_loss", ctypes.c_double),
        ("gross_loss_pct", ctypes.c_double),
        ("profit_factor", ctypes.c_double),
        ("avg_trade", ctypes.c_double),
        ("avg_trade_pct", ctypes.c_double),
        ("avg_win", ctypes.c_double),
        ("avg_win_pct", ctypes.c_double),
        ("avg_loss", ctypes.c_double),
        ("avg_loss_pct", ctypes.c_double),
        ("ratio_avg_win_avg_loss", ctypes.c_double),
        ("largest_win", ctypes.c_double),
        ("largest_win_pct", ctypes.c_double),
        ("largest_loss", ctypes.c_double),
        ("largest_loss_pct", ctypes.c_double),
        ("commission_paid", ctypes.c_double),
        ("expectancy", ctypes.c_double),
        ("max_consecutive_wins", ctypes.c_int32),
        ("max_consecutive_losses", ctypes.c_int32),
        ("avg_bars_in_trade", ctypes.c_double),
        ("avg_bars_in_wins", ctypes.c_double),
        ("avg_bars_in_losses", ctypes.c_double),
    ]


class EquityStatsC(ctypes.Structure):
    _fields_ = [
        ("max_equity_drawdown", ctypes.c_double),
        ("max_equity_drawdown_pct", ctypes.c_double),
        ("max_equity_runup", ctypes.c_double),
        ("max_equity_runup_pct", ctypes.c_double),
        ("buy_hold_return", ctypes.c_double),
        ("buy_hold_return_pct", ctypes.c_double),
        ("sharpe_tv", ctypes.c_double),
        ("sortino_tv", ctypes.c_double),
        ("sharpe_bar", ctypes.c_double),
        ("sortino_bar", ctypes.c_double),
        ("cagr", ctypes.c_double),
        ("calmar", ctypes.c_double),
        ("recovery_factor", ctypes.c_double),
        ("time_in_market_pct", ctypes.c_double),
        ("open_pl", ctypes.c_double),
    ]


class MetricsC(ctypes.Structure):
    _fields_ = [("all", TradeStatsC), ("longs", TradeStatsC), ("shorts", TradeStatsC), ("equity", EquityStatsC)]


class DiagC(ctypes.Structure):
    _fields_ = [
        ("sec_id", ctypes.c_int),
        ("feed_count", ctypes.c_int64),
        ("eval_complete_count", ctypes.c_int64),
        ("eval_partial_count", ctypes.c_int64),
    ]


class TraceC(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_int64),
        ("bar_index", ctypes.c_int32),
        ("name_id", ctypes.c_int32),
        ("value", ctypes.c_double),
    ]


class EquityPointC(ctypes.Structure):
    _fields_ = [("time_ms", ctypes.c_int64), ("equity", ctypes.c_double), ("open_profit", ctypes.c_double)]


class ReportC(ctypes.Structure):
    _fields_ = [
        ("total_trades", ctypes.c_int),
        ("trades", ctypes.POINTER(TradeC)),
        ("trades_len", ctypes.c_int),
        ("net_profit", ctypes.c_double),
        ("input_bars_processed", ctypes.c_int64),
        ("script_bars_processed", ctypes.c_int64),
        ("security_feeds_total", ctypes.c_int64),
        ("security_eval_complete_total", ctypes.c_int64),
        ("security_eval_partial_total", ctypes.c_int64),
        ("magnifier_sub_bars_total", ctypes.c_int64),
        ("magnifier_sample_ticks_total", ctypes.c_int64),
        ("input_tf_seconds", ctypes.c_int),
        ("script_tf_seconds", ctypes.c_int),
        ("script_tf_ratio", ctypes.c_int),
        ("needs_aggregation", ctypes.c_int),
        ("bar_magnifier_enabled", ctypes.c_int),
        ("security_diag", ctypes.POINTER(DiagC)),
        ("security_diag_len", ctypes.c_int),
        ("trace", ctypes.POINTER(TraceC)),
        ("trace_len", ctypes.c_int),
        ("trace_names", ctypes.POINTER(ctypes.c_char_p)),
        ("trace_names_len", ctypes.c_int),
        ("metrics", MetricsC),
        ("equity_curve", ctypes.POINTER(EquityPointC)),
        ("equity_curve_len", ctypes.c_int64),
    ]


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        result = run_backtest(request)
    except Exception as exc:  # noqa: BLE001 - process boundary must never throw raw tracebacks to Node
        result = fail("pineforge_native_runner_error", str(exc))
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0


def run_backtest(request: dict[str, Any]) -> dict[str, Any]:
    validate_request(request)
    started = time.perf_counter()
    output_dir = Path(request["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    pine_path = Path(request["pine_code_path"])
    csv_path = Path(request["ohlcv_csv_path"])
    pine_code = pine_path.read_text(encoding="utf-8")
    bars, csv_quality = read_bars(csv_path)
    limits = request.get("limits") or {}
    max_bars = int(limits.get("max_bars") or 1_578_240)
    if len(bars) > max_bars:
        return fail("pineforge_max_bars_exceeded", f"bars {len(bars)} exceeds max_bars {max_bars}")

    with tempfile.TemporaryDirectory(prefix="pineforge-native-") as temp_name:
        work_dir = Path(temp_name)
        generated_cpp = work_dir / "generated.cpp"
        strategy_so = work_dir / "strategy.so"
        compile_result = transpile_and_compile(pine_code, generated_cpp, strategy_so)
        if compile_result["status"] != "pass":
            write_json(output_dir / "compile.json", compile_result)
            return fail("pineforge_compile_failed", compile_result["message"], {"compile": compile_result})

        run_started = time.perf_counter()
        native = execute_strategy(strategy_so, bars, request)
        run_ms = int((time.perf_counter() - run_started) * 1000)

    report = build_report(request, pine_code, bars, native, compile_result, run_ms, csv_quality)
    trades = native["trades"]
    equity_curve = native["equity_curve"]
    write_runner_artifacts(output_dir, report, trades, equity_curve, compile_result)
    output_bytes = artifact_bytes(output_dir)
    max_output_bytes = int(limits.get("max_output_bytes") or 50_000_000)
    if output_bytes > max_output_bytes:
        return fail("pineforge_max_output_bytes_exceeded", f"output bytes {output_bytes} exceeds max_output_bytes {max_output_bytes}")

    return {
        "status": "pass",
        "runner": "pineforge-runner",
        "runner_version": RUNNER_VERSION,
        "report": report,
        "trades": trades,
        "equity_curve": equity_curve,
        "compile": compile_result,
        "artifact_manifest": manifest_for(output_dir),
        "stats": {
            "bars_processed": len(bars),
            "compile_ms": int(compile_result.get("compile_ms", 0)),
            "run_ms": run_ms,
            "output_bytes": output_bytes,
            "total_ms": int((time.perf_counter() - started) * 1000),
        },
    }


def transpile_and_compile(pine_code: str, generated_cpp: Path, strategy_so: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from pineforge_codegen import transpile
        from pineforge_codegen.errors import CompileError
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "stage": "import", "message": str(exc)}

    try:
        generated_cpp.write_text(transpile(pine_code), encoding="utf-8")
    except CompileError as exc:
        return {"status": "fail", "stage": "transpile", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "stage": "transpile", "message": str(exc)}

    command = [
        CXX,
        "-std=c++17",
        "-O3",
        "-fPIC",
        "-shared",
        str(generated_cpp),
        "-o",
        str(strategy_so),
        f"-I{PINEFORGE_INCLUDE_DIR}",
        f"-L{PINEFORGE_LIB_DIR}",
        "-lpineforge",
    ]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    compile_ms = int((time.perf_counter() - started) * 1000)
    if process.returncode != 0:
        return {
            "status": "fail",
            "stage": "compile",
            "message": process.stderr[-4000:] or process.stdout[-4000:] or f"{CXX} exited {process.returncode}",
            "compile_ms": compile_ms,
        }
    return {
        "status": "pass",
        "stage": "compile",
        "native_engine": True,
        "engine_version": ENGINE_VERSION,
        "codegen_version": CODEGEN_VERSION,
        "compile_ms": compile_ms,
    }


def execute_strategy(strategy_so: Path, bars: list[BarC], request: dict[str, Any]) -> dict[str, Any]:
    lib = ctypes.CDLL(str(strategy_so))
    lib.strategy_create.argtypes = [ctypes.c_char_p]
    lib.strategy_create.restype = ctypes.c_void_p
    lib.run_backtest_full.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(BarC),
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ReportC),
    ]
    lib.strategy_free.argtypes = [ctypes.c_void_p]
    lib.report_free.argtypes = [ctypes.POINTER(ReportC)]
    if hasattr(lib, "strategy_set_override"):
        lib.strategy_set_override.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    if hasattr(lib, "strategy_set_syminfo_mintick"):
        lib.strategy_set_syminfo_mintick.argtypes = [ctypes.c_void_p, ctypes.c_double]
    if hasattr(lib, "strategy_set_syminfo_pointvalue"):
        lib.strategy_set_syminfo_pointvalue.argtypes = [ctypes.c_void_p, ctypes.c_double]
    if hasattr(lib, "strategy_get_last_error"):
        lib.strategy_get_last_error.argtypes = [ctypes.c_void_p]
        lib.strategy_get_last_error.restype = ctypes.c_char_p

    config = request["config"]
    state = lib.strategy_create(b"{}")
    if not state:
        raise RuntimeError("strategy_create returned null")
    report = ReportC()
    try:
        applied_cost_model = set_strategy_overrides(lib, state, config, request, bars)
        array_type = BarC * len(bars)
        bar_array = array_type(*bars)
        lib.run_backtest_full(
            state,
            bar_array,
            len(bars),
            pine_timeframe(config.get("candle_timeframe") or "1m").encode("utf-8"),
            pine_timeframe(config["timeframe"]).encode("utf-8"),
            1,
            4,
            3,
            ctypes.byref(report),
        )
        if hasattr(lib, "strategy_get_last_error"):
            err = lib.strategy_get_last_error(state)
            if err:
                message = err.decode("utf-8", "replace")
                if message:
                    raise RuntimeError(message)
        return {
            "summary": report_summary(report),
            "trades": report_trades(report),
            "equity_curve": report_equity_curve(report),
            "applied_cost_model": applied_cost_model,
        }
    finally:
        lib.report_free(ctypes.byref(report))
        lib.strategy_free(state)


def set_strategy_overrides(lib: Any, state: int, config: dict[str, Any], request: dict[str, Any], bars: list[BarC]) -> dict[str, Any]:
    market_metadata = request.get("market_metadata") if isinstance(request.get("market_metadata"), dict) else {}
    mintick = positive_float(market_metadata.get("mintick"))
    pointvalue = positive_float(market_metadata.get("pointvalue"))
    if mintick is not None and hasattr(lib, "strategy_set_syminfo_mintick"):
        lib.strategy_set_syminfo_mintick(state, mintick)
    if pointvalue is not None and hasattr(lib, "strategy_set_syminfo_pointvalue"):
        lib.strategy_set_syminfo_pointvalue(state, pointvalue)
    applied = {
        "initial_capital": config["initial_capital"],
        "fee_bps": config["fee_bps"],
        "commission_type": "percent",
        "commission_value": float(config.get("fee_bps") or 0) / 100.0,
        "slippage_bps": config["slippage_bps"],
        "slippage_ticks": None,
        "slippage_mapped": False,
        "mintick": mintick,
        "pointvalue": pointvalue,
        "warnings": [],
    }
    if not hasattr(lib, "strategy_set_override"):
        applied["warnings"].append("strategy_set_override unavailable; cost overrides were not applied.")
        return applied
    overrides = {
        "initial_capital": config.get("initial_capital"),
        "commission_type": "percent",
        "commission_value": float(config.get("fee_bps") or 0) / 100.0,
    }
    slippage_bps = float(config.get("slippage_bps") or 0)
    if slippage_bps == 0:
        overrides["slippage"] = 0
        applied["slippage_ticks"] = 0
        applied["slippage_mapped"] = True
    elif mintick is not None and bars and bars[0].close > 0:
        ticks = max(1, round((bars[0].close * (slippage_bps / 10_000.0)) / mintick))
        overrides["slippage"] = ticks
        applied["slippage_ticks"] = ticks
        applied["slippage_mapped"] = True
    else:
        applied["warnings"].append("slippage_bps_not_mapped_to_ticks")
    for key, value in overrides.items():
        lib.strategy_set_override(state, str(key).encode("utf-8"), str(value).encode("utf-8"))
    return applied


def build_report(
    request: dict[str, Any],
    pine_code: str,
    bars: list[BarC],
    native: dict[str, Any],
    compile_result: dict[str, Any],
    run_ms: int,
    csv_quality: dict[str, Any],
) -> dict[str, Any]:
    summary = native["summary"]
    config = request["config"]
    applied_cost_model = native.get("applied_cost_model") or {}
    warnings = list(applied_cost_model.get("warnings") or [])
    return {
        "engine": "pineforge",
        "evidence_label": "PineForge local Pine preview evidence",
        "execution_semantics": "model_generated_pine_pineforge",
        "input": {"bars": len(bars), "symbol": config["symbol"]},
        "applied_runtime": {
            "input_tf": pine_timeframe(config.get("candle_timeframe") or "1m"),
            "script_tf": pine_timeframe(config["timeframe"]),
            "bar_magnifier": True,
            "native_engine": True,
        },
        "pineforge_runtime": {
            "input_tf": pine_timeframe(config.get("candle_timeframe") or "1m"),
            "script_tf": pine_timeframe(config["timeframe"]),
            "bar_magnifier": True,
            "bars_processed": len(bars),
        },
        "cost_model": {
            "initial_capital": config["initial_capital"],
            "fee_bps": config["fee_bps"],
            "commission_type": "percent",
            "commission_value": float(config.get("fee_bps") or 0) / 100.0,
            "slippage_bps": config["slippage_bps"],
        },
        "applied_cost_model": applied_cost_model,
        "source_feed_checksum": csv_quality["source_feed_checksum"],
        "csv_quality": csv_quality,
        "summary": summary,
        "metrics": {
            "trade_count": summary["total_trades"],
            "net_profit": summary["net_pnl"],
            "total_return": summary["net_profit_pct"],
            "max_drawdown": summary["max_drawdown"],
            "max_drawdown_pct": summary["max_drawdown_pct"],
            "win_rate": summary["win_rate_pct"],
            "sharpe": summary["sharpe"],
            "sortino": summary["sortino"],
        },
        "warnings": warnings,
        "assumptions": [
            "PineForge local Pine preview evidence; not TradingView official validation, broker proof, or live-trading evidence.",
            "Execution uses cached OHLCV supplied by strategy-codebot, not PineForge network fetches.",
        ],
        "compile": compile_result,
        "runner": {"version": RUNNER_VERSION, "engine_version": ENGINE_VERSION, "codegen_version": CODEGEN_VERSION},
        "runtime": {"run_ms": run_ms},
        "reproducibility_hash": hashlib.sha256(
            pine_code.encode("utf-8") + first_last_bar_hash_material(bars) + json.dumps(config, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def report_summary(report: ReportC) -> dict[str, Any]:
    stats = report.metrics.all
    equity = report.metrics.equity
    return {
        "total_trades": int(report.total_trades),
        "wins": int(stats.num_wins),
        "losses": int(stats.num_losses),
        "win_rate_pct": finite_or_none(stats.percent_profitable),
        "net_pnl": finite_or_none(report.net_profit),
        "net_profit_pct": finite_or_none(stats.net_profit_pct),
        "gross_profit": finite_or_none(stats.gross_profit),
        "gross_loss": finite_or_none(stats.gross_loss),
        "profit_factor": finite_or_none(stats.profit_factor),
        "max_drawdown": finite_or_none(equity.max_equity_drawdown),
        "max_drawdown_pct": finite_or_none(equity.max_equity_drawdown_pct),
        "sharpe": finite_or_none(equity.sharpe_tv),
        "sortino": finite_or_none(equity.sortino_tv),
        "bars_processed": int(report.input_bars_processed),
        "script_bars_processed": int(report.script_bars_processed),
        "input_tf_seconds": int(report.input_tf_seconds),
        "script_tf_seconds": int(report.script_tf_seconds),
    }


def report_trades(report: ReportC) -> list[dict[str, Any]]:
    trades = []
    for index in range(max(0, int(report.trades_len))):
        trade = report.trades[index]
        trades.append(
            {
                "index": index,
                "entry_time": int(trade.entry_time),
                "exit_time": int(trade.exit_time),
                "entry_price": finite_or_none(trade.entry_price),
                "exit_price": finite_or_none(trade.exit_price),
                "pnl": finite_or_none(trade.pnl),
                "pnl_pct": finite_or_none(trade.pnl_pct),
                "side": "long" if trade.is_long else "short",
                "qty": finite_or_none(trade.qty),
                "commission": finite_or_none(trade.commission),
                "max_runup": finite_or_none(trade.max_runup),
                "max_drawdown": finite_or_none(trade.max_drawdown),
                "entry_bar_index": int(trade.entry_bar_index),
                "exit_bar_index": int(trade.exit_bar_index),
            }
        )
    return trades


def report_equity_curve(report: ReportC) -> list[dict[str, Any]]:
    points = []
    for index in range(max(0, int(report.equity_curve_len))):
        point = report.equity_curve[index]
        points.append(
            {
                "index": index,
                "timestamp": int(point.time_ms),
                "equity": finite_or_none(point.equity),
                "open_profit": finite_or_none(point.open_profit),
            }
        )
    return points


def read_bars(csv_path: Path) -> tuple[list[BarC], dict[str, Any]]:
    raw = csv_path.read_text(encoding="utf-8")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = ["timestamp", "open", "high", "low", "close", "volume"]
        if reader.fieldnames != expected:
            raise ValueError(f"Invalid OHLCV CSV header: expected {','.join(expected)}")
        rows = list(reader)
    bars = []
    previous_timestamp: int | None = None
    for row in rows:
        timestamp = int(float(row["timestamp"]))
        open_ = finite_float(row["open"], "open")
        high = finite_float(row["high"], "high")
        low = finite_float(row["low"], "low")
        close = finite_float(row["close"], "close")
        volume = finite_float(row["volume"], "volume")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("Invalid OHLCV CSV: timestamps must be strictly ascending")
        if high < max(open_, close) or low > min(open_, close):
            raise ValueError(f"Invalid OHLCV CSV: malformed OHLC at timestamp {timestamp}")
        bars.append(BarC(open_, high, low, close, volume, timestamp))
        previous_timestamp = timestamp
    return bars, {
        "status": "pass",
        "rows": len(bars),
        "source_feed_checksum": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def write_runner_artifacts(
    output_dir: Path,
    report: dict[str, Any],
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    compile_result: dict[str, Any],
) -> None:
    write_json(output_dir / "pineforge-report-full.json", report)
    write_json(output_dir / "trades.json", trades)
    write_json(output_dir / "equity-curve.json", equity_curve)
    write_json(output_dir / "compile.json", compile_result)


def manifest_for(output_dir: Path) -> dict[str, str]:
    return {
        "report": str(output_dir / "pineforge-report-full.json"),
        "trades": str(output_dir / "trades.json"),
        "equity_curve": str(output_dir / "equity-curve.json"),
        "compile": str(output_dir / "compile.json"),
    }


def artifact_bytes(output_dir: Path) -> int:
    total = 0
    for name in ["pineforge-report-full.json", "trades.json", "equity-curve.json", "compile.json"]:
        path = output_dir / name
        if path.exists():
            total += path.stat().st_size
    return total


def validate_request(request: dict[str, Any]) -> None:
    for key in ["job_id", "run_id", "pine_code_path", "ohlcv_csv_path", "output_dir", "config"]:
        if key not in request:
            raise ValueError(f"missing {key}")
    for key in ["symbol", "timeframe", "initial_capital", "fee_bps", "slippage_bps"]:
        if key not in request["config"]:
            raise ValueError(f"missing config.{key}")


def pine_timeframe(value: str) -> str:
    value = value.strip().lower()
    if value.endswith("h"):
        return str(int(value[:-1]) * 60)
    if value.endswith("m"):
        return value[:-1]
    return value


def finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def finite_float(value: Any, field: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Invalid OHLCV CSV: non-finite {field}")
    return parsed


def positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def first_last_bar_hash_material(bars: list[BarC]) -> bytes:
    if not bars:
        return b""
    first = bars[0]
    last = bars[-1]
    return json.dumps(
        [
            [first.timestamp, first.open, first.high, first.low, first.close, first.volume],
            [last.timestamp, last.open, last.high, last.low, last.close, last.volume],
            len(bars),
        ],
        separators=(",", ":"),
    ).encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def fail(code: str, message: str, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if diagnostics:
        error["diagnostics"] = diagnostics
    return {"status": "fail", "error": error}


if __name__ == "__main__":
    raise SystemExit(main())
