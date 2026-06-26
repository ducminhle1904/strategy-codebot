from __future__ import annotations

from typing import Any


def format_backtest_summary_text(
    summary: dict[str, Any],
    *,
    language: str = "en",
    completed: bool = False,
    include_run_id: bool = False,
) -> str:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    pnl = metrics.get("pnl") if isinstance(metrics.get("pnl"), dict) else {}
    quality_flags = metrics.get("quality_flags") if isinstance(metrics.get("quality_flags"), list) else []
    quality_status = string_value(metrics.get("quality_status"))
    pnl_abs = format_metric_value(pnl.get("absolute"))
    pnl_pct = format_metric_value(pnl.get("percentage"), suffix="%")
    drawdown = format_metric_value(metrics.get("max_drawdown"), suffix="%")
    trade_count = format_metric_value(metrics.get("trade_count"))
    win_rate = format_metric_value(metrics.get("win_rate"), suffix="%")
    symbol = string_value(summary.get("symbol")) or "the strategy"
    signal_timeframe = string_value(summary.get("signal_timeframe")) or "unknown"
    candle_timeframe = string_value(summary.get("candle_timeframe")) or "unknown"
    evidence_label = user_facing_evidence_label(summary.get("evidence_label"))
    run_id = string_value(summary.get("run_id"))
    run_suffix = f" (run `{run_id}`)" if include_run_id and run_id else ""
    is_vi = _normalize_language(language) == "vi"
    prefix = (
        f"Backtest completed for {symbol}{run_suffix}"
        if completed
        else (f"Backtest summary cho {symbol}" if is_vi else f"Backtest summary for {symbol}")
    )
    caveat = (
        "không phải TradingView official validation, broker proof, live proof, hay cam kết lợi nhuận."
        if is_vi
        else "this is not TradingView official validation, broker proof, live proof, or a profitability claim."
    )
    quality_note = ""
    if quality_status == "fail" or "position_sizing_mismatch" in quality_flags:
        quality_note = (
            " Kết quả này đang không hợp lệ để đánh giá cho tới khi sửa position sizing."
            if is_vi
            else " This result is invalid for evaluation until position sizing is repaired."
        )
    return (
        f"{prefix}: PnL {pnl_abs} ({pnl_pct}), max drawdown {drawdown}, "
        f"{trade_count} trades, win rate {win_rate}. Signal timeframe {signal_timeframe}; "
        f"execution candles {candle_timeframe}. {evidence_label}; {caveat}{quality_note}"
    )


def format_metric_value(value: Any, *, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        formatted = f"{value:.4f}".rstrip("0").rstrip(".")
        return f"{formatted}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    text = string_value(value)
    return f"{text}{suffix}" if text else "N/A"


def string_value(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def user_facing_evidence_label(value: Any) -> str:
    text = string_value(value)
    if not text:
        return "local sandbox preview evidence"
    lowered = text.lower()
    if "pineforge" in lowered or "engine" in lowered:
        return "local sandbox preview evidence"
    return text


def _normalize_language(language: str | None) -> str:
    return "vi" if isinstance(language, str) and language.lower().startswith("vi") else "en"
