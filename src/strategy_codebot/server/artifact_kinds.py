BACKTEST_DASHBOARD_ARTIFACT_KIND = "backtest_dashboard"
BACKTEST_PLAN_ARTIFACT_KIND = "backtest_plan"
BACKTEST_REPORT_ARTIFACT_KIND = "backtest_report"
BACKTEST_RUN_METADATA_ARTIFACT_KIND = "backtest_run_metadata"
BACKTEST_TRADES_ARTIFACT_KIND = "backtest_trades"
BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND = "backtest_variant_comparison"
PROPOSED_ORDER_INTENT_ARTIFACT_KIND = "proposed_order_intent"
RISK_GATE_REPORT_ARTIFACT_KIND = "risk_gate_report"
ROBUSTNESS_REPORT_ARTIFACT_KIND = "robustness_report"

USER_ARTIFACT_KINDS = {
    BACKTEST_REPORT_ARTIFACT_KIND,
    BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND,
    "knowledge_proposal",
    "pine_strategy_source",
    "pine_file",
    "mql5_file",
    PROPOSED_ORDER_INTENT_ARTIFACT_KIND,
    "review_report",
    RISK_GATE_REPORT_ARTIFACT_KIND,
    ROBUSTNESS_REPORT_ARTIFACT_KIND,
    "manual_checklist",
}

INTERNAL_ARTIFACT_KINDS = {
    "agent_run",
    "harness_evidence_summary",
    "knowledge_context",
    "live_error",
    "live_metadata",
    "live_workflow_trace",
    "quality_report",
    "runtime_trace_summary",
    "backtest_equity_curve",
    BACKTEST_PLAN_ARTIFACT_KIND,
    BACKTEST_RUN_METADATA_ARTIFACT_KIND,
    "backtest_source_bundle",
    "backtest_strategy_adapter_source",
    BACKTEST_TRADES_ARTIFACT_KIND,
    "backtest_ohlcv_metadata",
    "candle_cache_manifest",
    "market_data_cache_manifest",
    "market_data_ohlcv_metadata",
    "pineforge_compile_report",
    "pineforge_runner_manifest",
    "pineforge_validation_report",
    "validation_report",
}

REPORT_ARTIFACT_KINDS = {
    BACKTEST_REPORT_ARTIFACT_KIND,
    BACKTEST_VARIANT_COMPARISON_ARTIFACT_KIND,
    PROPOSED_ORDER_INTENT_ARTIFACT_KIND,
    "review_report",
    RISK_GATE_REPORT_ARTIFACT_KIND,
    ROBUSTNESS_REPORT_ARTIFACT_KIND,
    "validation_report",
    "manual_checklist",
    "pineforge_compile_report",
}

EVIDENCE_ARTIFACT_KINDS = {
    "harness_evidence_summary",
    "market_data_cache_manifest",
    "market_data_ohlcv_metadata",
    "pineforge_runner_manifest",
}
TRACE_ARTIFACT_KINDS = {"runtime_trace_summary", "live_workflow_trace", "agent_run", "live_error", "live_metadata"}
