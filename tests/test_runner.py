from pathlib import Path
import json

import pytest

from strategy_codebot.live import WORKFLOW_SINGLE, LiveProviderError, LiveGenerationResult, LiveRunOptions
from strategy_codebot.knowledge_context import build_knowledge_context
from strategy_codebot.knowledge_base import build_knowledge_index
from strategy_codebot.pine import generate_pine
from strategy_codebot.runner import _harness_trace_decisions, _harness_trace_friction, _harness_trace_token_estimate, run_strategy
from strategy_codebot.schemas import load_json
from strategy_codebot.tool_runtime import ToolBlockedError, ToolHarness


def test_dry_run_creates_pine_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "pine-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=False,
    )

    assert result["status"] == "pass"
    assert (out_dir / "strategy-spec.json").exists()
    assert (out_dir / "pine" / "strategy.pine").exists()
    assert (out_dir / "manual-tradingview-checklist.md").exists()
    assert load_json(out_dir / "validation-report.json")["platform"] == "pine_v6"
    assert load_json(out_dir / "agent-run.json")["status"] == "pass"
    assert (out_dir / "runtime-trace.jsonl").exists()
    assert load_json(out_dir / "runtime-summary.json")["policy_mode"] == "observe"
    assert not (out_dir / "review-report.json").exists()


def test_run_strategy_implicitly_skips_unavailable_harness_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "skip-harness-run"
    monkeypatch.setattr(
        "strategy_codebot.runner.harness_cli_availability",
        lambda: {"available": False, "status": "not_executable", "reason": "exec format error"},
    )
    monkeypatch.setattr("strategy_codebot.runner.should_record_harness", lambda requested: False if requested is None else requested)

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=None,
    )

    agent_run = load_json(out_dir / "agent-run.json")
    assert result["status"] == "pass"
    assert agent_run["harness_recording_status"] == "skipped_unavailable"
    assert agent_run["harness_recording_reason"] == "exec format error"


def test_combined_target_creates_mql5_runner_design(tmp_path: Path) -> None:
    out_dir = tmp_path / "both-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-both.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=False,
    )

    report = load_json(out_dir / "validation-report.json")
    assert result["status"] == "manual_required"
    assert report["platform"] == "both"
    assert (out_dir / "mql5" / "runner-design.md").exists()
    assert "MetaTrader 5" in (out_dir / "mql5" / "runner-design.md").read_text()


def test_nautilus_target_creates_runtime_artifacts_without_live_execution(tmp_path: Path) -> None:
    out_dir = tmp_path / "nautilus-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-nautilus.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        record_harness=False,
    )

    manifest = load_json(out_dir / "nautilus" / "runtime-manifest.json")
    report = load_json(out_dir / "validation-report.json")
    assert result["status"] == "pass"
    assert report["platform"] == "nautilus_py"
    assert (out_dir / "nautilus" / "strategy.py").exists()
    assert (out_dir / "nautilus" / "parity-report.json").exists()
    assert manifest["live_enabled"] is False
    assert manifest["safety"]["live_broker_execution"] == "blocked_until_explicit_decision"


def test_invalid_mode_does_not_create_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "bad-mode"

    with pytest.raises(ValueError):
        run_strategy(
            spec_path=Path("examples/specs/ma-crossover-pine.json"),
            prompt=None,
            mode="bad",
            out_dir=out_dir,
            record_harness=False,
        )

    assert not out_dir.exists()


def test_missing_spec_does_not_create_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "missing-spec"

    with pytest.raises(FileNotFoundError):
        run_strategy(
            spec_path=tmp_path / "missing.json",
            prompt=None,
            mode="dry-run",
            out_dir=out_dir,
            record_harness=False,
        )

    assert not out_dir.exists()


def test_detailed_harness_trace_helpers_are_deterministic() -> None:
    decisions = _harness_trace_decisions(
        mode="live",
        spec={"target_platform": "pine_v6"},
        review="parallel",
        policy="enforce",
        runtime_trace=True,
        live_options=LiveRunOptions(workflow=WORKFLOW_SINGLE, cost_profile="cheap"),
    )
    live_result = LiveGenerationResult(
        strategy_spec={"target_platform": "pine_v6"},
        pine_code="//@version=6",
        model="openai/test",
        provider="openai",
        latency_ms=1,
        usage={"total_tokens": 17},
    )
    harness = ToolHarness(run_id="helper-test")

    assert decisions == [
        "mode=live",
        "target_platform=pine_v6",
        "review=parallel",
        "policy=enforce",
        "runtime_trace=true",
        "workflow=single",
        "cost_profile=cheap",
    ]
    assert _harness_trace_token_estimate(live_result) == 17
    assert _harness_trace_token_estimate(None) == 0
    assert _harness_trace_friction(harness) == "none"

    harness.events.append({"event_type": "tool.failed", "tool_id": "validate_pine_static", "status": "fail"})

    assert _harness_trace_friction(harness) == "runtime tool failures or policy blocks recorded"


def test_integrated_parallel_review_creates_review_artifact(tmp_path: Path) -> None:
    out_dir = tmp_path / "review-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        review="parallel",
        record_harness=False,
    )

    agent_run = load_json(out_dir / "agent-run.json")
    assert result["status"] == "pass"
    assert load_json(out_dir / "review-report.json")["run_status"] == "completed"
    assert "review-report.json" in agent_run["output_refs"]
    assert "runtime-trace.jsonl" in agent_run["output_refs"]
    assert "parallel-review" in agent_run["tool_calls"]


def test_record_harness_parallel_review_uses_shared_intake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "harness-review-run"
    trace_commands = []
    intake_calls = []

    def fake_record_trace_intake(**kwargs):
        intake_calls.append(kwargs)
        return 99

    def fake_record_trace(command):
        trace_commands.append(command)

    monkeypatch.setattr("strategy_codebot.runner.record_trace_intake", fake_record_trace_intake)
    monkeypatch.setattr("strategy_codebot.runner.record_trace", fake_record_trace)
    monkeypatch.setattr("strategy_codebot.review.record_trace", fake_record_trace)

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        review="parallel",
        record_harness=True,
    )

    assert result["status"] == "pass"
    assert len(intake_calls) == 1
    assert intake_calls[0]["input_type"] == "new spec"
    assert len(trace_commands) == 2
    assert all(command[command.index("--intake") + 1] == "99" for command in trace_commands)
    assert all("--errors" in command for command in trace_commands)
    assert all("--friction" in command for command in trace_commands)
    assert all("--duration" in command for command in trace_commands)
    assert all("--tokens" in command for command in trace_commands)
    assert all("--decisions" in command for command in trace_commands)


def test_no_runtime_trace_preserves_phase_2_artifact_shape(tmp_path: Path) -> None:
    out_dir = tmp_path / "no-runtime-trace"

    run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        review="parallel",
        runtime_trace=False,
        record_harness=False,
    )

    agent_run = load_json(out_dir / "agent-run.json")
    assert not (out_dir / "runtime-trace.jsonl").exists()
    assert not (out_dir / "runtime-summary.json").exists()
    assert "runtime-trace.jsonl" not in agent_run["output_refs"]


def test_live_run_writes_quality_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-quality"
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "forex",
        "symbol": "EURUSD",
        "timeframe": "1d",
        "entry_rules": ["Enter long when fast SMA crosses above slow SMA."],
        "exit_rules": ["Exit with strategy.exit stop loss and take profit."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "2 ATR stop",
        "take_profit": "2R target",
    }

    def fake_generate_live(*args, **kwargs):
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="openrouter/google/gemini-2.5-flash",
            provider="openrouter",
            latency_ms=1,
            workflow="multi-agent",
            production_gate={"status": "pass", "validation_status": "pass"},
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    result = run_strategy(
        spec_path=None,
        prompt="Create a Pine strategy",
        mode="live",
        out_dir=out_dir,
        record_harness=False,
        live_options=LiveRunOptions(save_raw_provider=False),
    )

    metadata = load_json(out_dir / "live-metadata.json")
    quality = load_json(out_dir / "quality-report.json")
    agent_run = load_json(out_dir / "agent-run.json")

    assert result["status"] == "pass"
    assert quality["status"] == "pass"
    assert metadata["quality_status"] == "pass"
    assert metadata["production_gate"]["quality_blocker_count"] == 0
    assert "quality-report.json" in agent_run["validation_refs"]


def test_live_run_writes_redacted_proxy_attribution_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-proxy-attribution"
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "crypto",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "entry_rules": ["Enter long after a liquidity sweep reclaim."],
        "exit_rules": ["Exit with strategy.exit stop loss and take profit."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "Structure low invalidation",
        "take_profit": "2R target",
    }

    def fake_generate_live(*args, **kwargs):
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="litellm_proxy/paid_low.pine_code_generation",
            provider="litellm",
            latency_ms=1200,
            workflow="multi-agent",
            production_gate={"status": "pass", "validation_status": "pass"},
            attempts=[
                {
                    "stage": "pine_code_generation",
                    "model": "litellm_proxy/paid_low.pine_code_generation",
                    "route_model": "paid_low.pine_code_generation",
                    "gateway": "litellm_proxy",
                    "started_at": "2026-06-18T00:00:00Z",
                    "completed_at": "2026-06-18T00:00:01Z",
                    "provider_call_ms": 1190,
                    "stage_total_ms": 1200,
                    "provider_call_ratio": 0.99,
                    "local_processing_ms": 10,
                    "stage_input_chars": 9000,
                    "output_chars": 1800,
                    "status": "pass",
                    "prompt": "raw prompt must not be mirrored",
                    "raw_response": {"text": "raw response must not be mirrored"},
                    "headers": {"Authorization": "Bearer sk-secret123456789"},
                }
            ],
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    run_strategy(
        spec_path=None,
        prompt="Create a price-action Pine strategy",
        mode="live",
        out_dir=out_dir,
        record_harness=False,
        live_options=LiveRunOptions(save_raw_provider=False),
    )

    artifact = out_dir / "proxy-attribution-events.jsonl"
    assert artifact.exists()
    raw_text = artifact.read_text(encoding="utf-8")
    event = json.loads(raw_text)

    assert event["run_id"] == out_dir.name
    assert event["stage"] == "pine_code_generation"
    assert event["route_model"] == "paid_low.pine_code_generation"
    assert event["provider_call_ratio"] == 0.99
    assert "raw prompt" not in raw_text
    assert "raw response" not in raw_text
    assert "sk-secret" not in raw_text
    assert "Authorization" not in raw_text


def test_live_run_writes_knowledge_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-knowledge"
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "forex",
        "symbol": "EURUSD",
        "timeframe": "1d",
        "entry_rules": ["Enter long when fast SMA crosses above slow SMA."],
        "exit_rules": ["Exit with strategy.exit stop loss and take profit."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "2 ATR stop",
        "take_profit": "2R target",
    }
    knowledge_context = build_knowledge_context("Create a Pine v6 strategy")

    def fake_generate_live(*args, **kwargs):
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="openrouter/google/gemini-2.5-flash",
            provider="openrouter",
            latency_ms=1,
            workflow="multi-agent",
            production_gate={"status": "pass", "validation_status": "pass"},
            knowledge_context=knowledge_context,
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    run_strategy(
        spec_path=None,
        prompt="Create a Pine strategy",
        mode="live",
        out_dir=out_dir,
        record_harness=False,
        live_options=LiveRunOptions(save_raw_provider=False),
    )

    metadata = load_json(out_dir / "live-metadata.json")
    agent_run = load_json(out_dir / "agent-run.json")

    assert (out_dir / "knowledge-context.json").exists()
    assert metadata["knowledge_context_ref"] == "knowledge-context.json"
    assert "pine_v6_rules" in metadata["knowledge_doc_ids"]
    assert "doc:pine_v6_rules" in agent_run["retrieved_sources"]


def test_live_run_writes_retrieved_kb_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-kb-knowledge"
    index_path = tmp_path / "kb" / "index.json"
    build_knowledge_index(index_path=index_path)
    monkeypatch.setenv("STRATEGY_CODEBOT_KNOWLEDGE_INDEX", str(index_path))
    spec = {
        "target_platform": "pine_v6",
        "script_type": "strategy",
        "market": "forex",
        "symbol": "EURUSD",
        "timeframe": "1d",
        "entry_rules": ["Enter after break of structure retest on a confirmed candle."],
        "exit_rules": ["Exit with strategy.exit stop loss and take profit."],
        "risk_rules": ["Risk 1% account equity per trade."],
        "position_sizing": "1% account equity risk per trade",
        "stop_loss": "Below retest swing",
        "take_profit": "2R target",
    }
    knowledge_context = build_knowledge_context("Create a price action strategy using break of structure and retest")

    def fake_generate_live(*args, **kwargs):
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="openrouter/google/gemini-2.5-flash",
            provider="openrouter",
            latency_ms=1,
            workflow="multi-agent",
            production_gate={"status": "pass", "validation_status": "pass"},
            knowledge_context=knowledge_context,
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    run_strategy(
        spec_path=None,
        prompt="Create a price action strategy using break of structure and retest",
        mode="live",
        out_dir=out_dir,
        record_harness=False,
        live_options=LiveRunOptions(save_raw_provider=False),
    )

    context = load_json(out_dir / "knowledge-context.json")
    metadata = load_json(out_dir / "live-metadata.json")
    agent_run = load_json(out_dir / "agent-run.json")

    assert context["store"] == "knowledge_base"
    assert context["retrieved_chunks"]
    assert metadata["knowledge_chunk_ids"]
    assert any(ref.startswith("doc:pattern-price-action") for ref in agent_run["retrieved_sources"])


def test_enforce_policy_allows_negative_live_trading_constraints(tmp_path: Path) -> None:
    out_dir = tmp_path / "enforce-run"

    result = run_strategy(
        spec_path=Path("examples/specs/ma-crossover-pine.json"),
        prompt=None,
        mode="dry-run",
        out_dir=out_dir,
        policy="enforce",
        record_harness=False,
    )

    assert result["status"] == "pass"
    assert load_json(out_dir / "runtime-summary.json")["policy_mode"] == "enforce"


def test_invalid_review_mode_does_not_create_output_directory(tmp_path: Path) -> None:
    out_dir = tmp_path / "bad-review"

    with pytest.raises(ValueError):
        run_strategy(
            spec_path=Path("examples/specs/ma-crossover-pine.json"),
            prompt=None,
            mode="dry-run",
            out_dir=out_dir,
            review="serial",
            record_harness=False,
        )

    assert not out_dir.exists()


def test_live_run_creates_artifacts_with_mocked_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-run"
    otel_path = tmp_path / "live-run-otel.jsonl"

    def fake_generate_live(*args, **kwargs) -> LiveGenerationResult:
        spec = load_json(Path("examples/specs/ma-crossover-pine.json"))
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="openai/test-model",
            provider="openai",
            latency_ms=42,
            usage={"total_tokens": 100},
            raw_response={"id": "raw-response"},
            workflow="multi-agent",
            stages=[{"stage": "balanced_review", "agent_role": "critic", "model": "openai/test-model", "provider": "openai", "latency_ms": 42, "usage": {"total_tokens": 100}}],
            workflow_trace={
                "workflow": "multi-agent",
                "lifecycle_events": [
                    {
                        "event_id": "evt-1",
                        "sequence": 1,
                        "created_at": "2026-06-16T00:00:00+00:00",
                        "run_id": "live-run",
                        "event_type": "agent.started",
                        "policy_mode": "observe",
                        "workflow": "multi-agent",
                        "stage": "strategy_reasoning",
                        "agent_role": "trading_analyst",
                        "status": "started",
                    }
                ],
                "stages": [],
                "final_decision": {"status": "pass"},
            },
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    result = run_strategy(
        spec_path=None,
        prompt="Create a Pine strategy",
        mode="live",
        out_dir=out_dir,
        record_harness=False,
        live_options=LiveRunOptions(save_raw_provider=True),
        otel_export=otel_path,
    )

    agent_run = load_json(out_dir / "agent-run.json")
    assert result["status"] == "pass"
    assert agent_run["provider"] == "openai"
    assert agent_run["model"] == "openai/test-model"
    assert (out_dir / "pine" / "strategy.pine").exists()
    assert (out_dir / "live-workflow-trace.json").exists()
    assert load_json(out_dir / "live-metadata.json")["usage"]["total_tokens"] == 100
    assert load_json(out_dir / "live-metadata.json")["workflow"] == "multi-agent"
    assert load_json(out_dir / "live-provider-response.json")["id"] == "raw-response"
    assert "agent.started" in (out_dir / "runtime-trace.jsonl").read_text(encoding="utf-8")
    assert "gen_ai.agent.name" in otel_path.read_text(encoding="utf-8")


def test_live_single_workflow_trace_does_not_reference_missing_workflow_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "live-single-run"

    def fake_generate_live(*args, **kwargs) -> LiveGenerationResult:
        spec = load_json(Path("examples/specs/ma-crossover-pine.json"))
        return LiveGenerationResult(
            strategy_spec=spec,
            pine_code=generate_pine(spec),
            model="openai/test-model",
            provider="openai",
            latency_ms=42,
            workflow="single",
        )

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    run_strategy(
        spec_path=None,
        prompt="Create a Pine strategy",
        mode="live",
        out_dir=out_dir,
        live_options=LiveRunOptions(workflow=WORKFLOW_SINGLE),
        record_harness=False,
    )

    assert not (out_dir / "live-workflow-trace.json").exists()
    assert "live-workflow-trace.json" not in (out_dir / "runtime-trace.jsonl").read_text(encoding="utf-8")


def test_run_strategy_rejects_old_live_kwargs(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        run_strategy(
            spec_path=None,
            prompt="Create a Pine strategy",
            mode="live",
            out_dir=tmp_path / "live-run",
            workflow=WORKFLOW_SINGLE,  # type: ignore[call-arg]
        )


def test_live_run_failure_writes_diagnostic_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "failed-live-run"
    otel_path = tmp_path / "failed-live-otel.jsonl"

    def fake_generate_live(*args, **kwargs) -> LiveGenerationResult:
        attempts = [{"stage": "strategy_reasoning", "status": "fail", "error_code": "provider_error", "failure_class": "provider_error", "model": "openai/test", "provider": "openai"}]
        diagnostics = {
            "workflow": "multi-agent",
            "attempts": attempts,
            "stage_records": [],
            "raw_responses": {"stages": {"strategy_reasoning": {"id": "raw"}}},
            "metadata": {"status": "fail", "workflow": "multi-agent", "model": "openai/test", "provider": "openai", "attempts": attempts, "stages": [], "repair_count": 0},
            "workflow_trace": {
                "run_id": "failed-live-run",
                "workflow": "multi-agent",
                "lifecycle_events": [
                    {
                        "event_id": "evt-1",
                        "sequence": 1,
                        "created_at": "2026-06-16T00:00:00+00:00",
                        "run_id": "failed-live-run",
                        "event_type": "agent.started",
                        "policy_mode": "observe",
                        "workflow": "multi-agent",
                        "stage": "strategy_reasoning",
                        "agent_role": "trading_analyst",
                        "status": "started",
                    }
                ],
                "attempts": attempts,
                "stages": [],
                "final_decision": {"status": "fail", "failure_class": "provider_error", "failure_stage": "strategy_reasoning"},
            },
            "final_decision": {"status": "fail", "failure_class": "provider_error", "failure_stage": "strategy_reasoning"},
        }
        raise LiveProviderError("provider failed", attempts=attempts, diagnostics=diagnostics)

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    with pytest.raises(LiveProviderError):
        run_strategy(
            spec_path=None,
            prompt="Create a Pine strategy",
            mode="live",
            out_dir=out_dir,
            record_harness=False,
            live_options=LiveRunOptions(save_raw_provider=True),
            otel_export=otel_path,
        )

    assert load_json(out_dir / "agent-run.json")["status"] == "fail"
    assert load_json(out_dir / "live-error.json")["diagnostics"]["final_decision"]["failure_stage"] == "strategy_reasoning"
    assert load_json(out_dir / "live-metadata.json")["status"] == "fail"
    assert (out_dir / "live-workflow-trace.json").exists()
    assert (out_dir / "live-provider-response.json").exists()
    assert (out_dir / "runtime-trace.jsonl").exists()
    assert otel_path.read_text(encoding="utf-8").strip()
    assert not (out_dir / "strategy-spec.json").exists()
    assert not (out_dir / "pine" / "strategy.pine").exists()


def test_live_run_compact_free_timeout_writes_failure_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "failed-free-live-run"
    attempts = [
        {
            "stage": "compact_free",
            "status": "fail",
            "error_code": "provider_timeout",
            "failure_class": "provider_timeout",
            "model": "openrouter/qwen/qwen3-coder:free",
            "provider": "openrouter",
            "latency_ms": 46000,
        }
    ]
    knowledge_context = {
        "mode": "auto",
        "knowledge_context_status": "degraded",
        "knowledge_health_status": "degraded",
        "failure_class": "knowledge_unavailable",
        "internal_docs": [{"id": "pine_v6_rules", "path": "docs/trading/pine-v6-rules.md"}],
        "external_refs": [],
        "context_refs": ["docs/trading/pine-v6-rules.md"],
    }

    def fake_generate_live(*args, **kwargs) -> LiveGenerationResult:
        diagnostics = {
            "workflow": "compact-free",
            "attempts": attempts,
            "stage_records": [],
            "knowledge_context_artifact": knowledge_context,
            "metadata": {
                "status": "fail",
                "workflow": "compact-free",
                "user_tier": "free",
                "model": "openrouter/qwen/qwen3-coder:free",
                "provider": "openrouter",
                "attempts": attempts,
                "stages": [],
                "repair_count": 0,
                "knowledge_context_status": "degraded",
                "knowledge_failure_class": "knowledge_unavailable",
                "free_capacity_status": "available",
                "selected_free_models": ["openrouter/qwen/qwen3-coder:free"],
            },
            "workflow_trace": {
                "run_id": "failed-free-live-run",
                "workflow": "compact-free",
                "attempts": attempts,
                "stages": [],
                "final_decision": {"status": "fail", "failure_class": "provider_timeout", "failure_stage": "compact_free"},
            },
            "final_decision": {"status": "fail", "failure_class": "provider_timeout", "failure_stage": "compact_free"},
        }
        raise LiveProviderError("compact free timed out", attempts=attempts, diagnostics=diagnostics)

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    with pytest.raises(LiveProviderError):
        run_strategy(
            spec_path=None,
            prompt="Create a Pine strategy",
            mode="live",
            out_dir=out_dir,
            record_harness=False,
            live_options=LiveRunOptions(user_tier="free", save_raw_provider=False),
        )

    metadata = load_json(out_dir / "live-metadata.json")
    error = load_json(out_dir / "live-error.json")

    assert metadata["workflow"] == "compact-free"
    assert metadata["user_tier"] == "free"
    assert metadata["knowledge_context_status"] == "degraded"
    assert metadata["knowledge_failure_class"] == "knowledge_unavailable"
    assert error["diagnostics"]["final_decision"]["failure_class"] == "provider_timeout"
    assert (out_dir / "knowledge-context.json").exists()
    assert not (out_dir / "strategy-spec.json").exists()


def test_live_run_enforce_blocks_prohibited_prompt_before_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "blocked-live-run"
    called = False

    def fake_generate_live(*args, **kwargs) -> LiveGenerationResult:
        nonlocal called
        called = True
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("strategy_codebot.runner.generate_live", fake_generate_live)

    with pytest.raises(ToolBlockedError):
        run_strategy(
            spec_path=None,
            prompt="Create a strategy that guarantees profit in live trading.",
            mode="live",
            out_dir=out_dir,
            policy="enforce",
            record_harness=False,
        )

    assert called is False
    assert not out_dir.exists()
