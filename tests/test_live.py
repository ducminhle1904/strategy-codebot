from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from strategy_codebot import live as live_module
from strategy_codebot.live import (
    COST_PROFILE_CHEAP,
    WORKFLOW_MULTI_AGENT,
    WORKFLOW_SINGLE,
    LiveCredentialError,
    LiveProviderError,
    LiveRunOptions,
    LiveSafetyError,
    generate_live,
    normalize_live_options,
)
from strategy_codebot.pine import generate_pine
from strategy_codebot.schemas import load_strategy_spec
from strategy_codebot.tool_runtime import contains_blocked_claim, find_blocked_claims, find_policy_claims


def _spec() -> dict[str, Any]:
    return load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))


def _final_payload(*, spec: dict | None = None, pine_code: str | None = None) -> dict[str, Any]:
    strategy_spec = spec or _spec()
    return {"strategy_spec": strategy_spec, "pine_code": pine_code or generate_pine(strategy_spec)}


def _stage_payload(stage: str, output: dict[str, Any], *, handoff_notes: str = "ready") -> dict[str, Any]:
    return {
        "stage": stage,
        "output": output,
        "assumptions": [],
        "handoff_notes": handoff_notes,
        "policy_observations": [],
    }


def _response(payload: dict[str, Any], *, tokens: int = 123) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"total_tokens": tokens}}


def _install_litellm(monkeypatch: pytest.MonkeyPatch, completion) -> None:
    module = types.SimpleNamespace(completion=completion)
    monkeypatch.setitem(sys.modules, "litellm", module)


def _set_quality_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")


def _workflow_response_for_name(name: str, *, review_verdict: str = "pass") -> dict[str, Any]:
    spec = _spec()
    pine_code = generate_pine(spec)
    if name == "strategy_codebot_strategy_reasoning":
        return _stage_payload(
            "strategy_reasoning",
            {
                "summary": "MA crossover strategy brief",
                "constraints": ["Pine v6 only"],
                "indicators": ["fast SMA", "slow SMA"],
                "entries": ["fast SMA crosses above slow SMA"],
                "exits": ["fast SMA crosses below slow SMA"],
                "risk_rules": ["fixed position sizing", "stop loss"],
                "non_goals": ["live trading automation"],
            },
        )
    if name == "strategy_codebot_strategy_coding":
        return _stage_payload("strategy_coding", {"strategy_spec": spec})
    if name == "strategy_codebot_pine_code_generation":
        return _stage_payload("pine_code_generation", {"pine_code": pine_code})
    if name == "strategy_codebot_balanced_review":
        fixes = ["add explicit strategy.exit"] if review_verdict != "pass" else []
        return _stage_payload("balanced_review", {"verdict": review_verdict, "required_fixes": fixes, "rationale": "reviewed"})
    if name == "strategy_codebot_repair":
        return _stage_payload("repair", {"strategy_spec": spec, "pine_code": pine_code})
    if name == "strategy_codebot_live_generation":
        return _final_payload(spec=spec, pine_code=pine_code)
    raise AssertionError(f"unexpected schema name {name}")


def test_live_run_options_defaults_and_legacy_normalization() -> None:
    defaults = LiveRunOptions()

    assert defaults.workflow == WORKFLOW_MULTI_AGENT
    assert defaults.cost_profile == "quality"
    assert defaults.model_stage_overrides == {}
    assert defaults.save_raw_provider is False
    assert defaults.knowledge_context == "auto"

    single = normalize_live_options(model_override="openai/test-model", workflow=WORKFLOW_SINGLE, save_raw_provider=True, knowledge_context="off")
    assert single.model_override == "openai/test-model"
    assert single.workflow == WORKFLOW_SINGLE
    assert single.save_raw_provider is True
    assert single.knowledge_context == "off"

    with pytest.raises(ValueError):
        LiveRunOptions(model_override="openai/test-model")

    with pytest.raises(ValueError, match="live_options cannot be combined"):
        normalize_live_options(LiveRunOptions(), workflow=WORKFLOW_SINGLE)


def test_strategy_response_schema_is_openai_strict_and_nullable_for_optional_fields() -> None:
    strategy_schema = live_module._strategy_schema()

    assert set(strategy_schema["required"]) == set(strategy_schema["properties"])
    assert "null" in strategy_schema["properties"]["symbol"]["type"]
    assert "default" not in strategy_schema["properties"]["assumptions"]


def test_stage_validation_prunes_nullable_optional_strategy_fields() -> None:
    spec = _spec()
    spec["symbol"] = None
    spec["position_sizing"] = None
    payload = _stage_payload("strategy_coding", {"strategy_spec": spec})

    live_module._validate_stage_payload("strategy_coding", payload)

    assert "symbol" not in payload["output"]["strategy_spec"]
    assert "position_sizing" not in payload["output"]["strategy_spec"]


def test_pine_version_header_normalizer_moves_single_v6_directive() -> None:
    code = "// license\n// author\n\n//@version=6\nstrategy(\"x\")\n"
    normalized, action = live_module._normalize_pine_version_header(code)

    assert normalized.startswith("//@version=6\n// license")
    assert action["changed"] is True
    assert action["from_line"] == 4


def test_pine_version_header_normalizer_does_not_upgrade_v5() -> None:
    code = "// license\n//@version=5\nstrategy(\"x\")\n"
    normalized, action = live_module._normalize_pine_version_header(code)

    assert normalized == code
    assert action["changed"] is False
    assert action["reason"] == "version_directive_not_v6"


def test_pine_version_header_normalizer_leaves_multiple_directives() -> None:
    code = "//@version=6\n//@version=6\nstrategy(\"x\")\n"
    normalized, action = live_module._normalize_pine_version_header(code)

    assert normalized == code
    assert action["changed"] is False
    assert action["reason"] == "missing_or_multiple_version_directives"


def test_live_stage_prompts_prioritize_strategy_exit_repairs() -> None:
    reasoning_messages = live_module._stage_messages(live_module.STAGE_STRATEGY_REASONING, {"prompt": "x"}, repair_iteration=None)
    coding_messages = live_module._stage_messages(live_module.STAGE_STRATEGY_CODING, {"prompt": "x"}, repair_iteration=None)
    pine_messages = live_module._stage_messages(live_module.STAGE_PINE_CODE_GENERATION, {"prompt": "x"}, repair_iteration=None)
    repair_messages = live_module._stage_messages(live_module.STAGE_REPAIR, {"prompt": "x"}, repair_iteration=1)

    assert "stop-loss" in reasoning_messages[0]["content"]
    assert "take_profit" in coding_messages[0]["content"]
    assert "strategy.exit" in pine_messages[0]["content"]
    assert "stop-loss" in repair_messages[0]["content"]
    assert "static validation failures first" in repair_messages[0]["content"]
    assert "1-2% account equity risk per trade" in reasoning_messages[0]["content"]
    assert "1-2% account equity risk per trade" in coding_messages[0]["content"]
    assert "1-2% account equity risk per trade" in repair_messages[0]["content"]


def test_position_sizing_quality_guard_rejects_full_capital_sizing() -> None:
    spec = _spec()

    for phrase in ("100% of available capital", "all capital", "entire account"):
        unsafe = {**spec, "position_sizing": f"Fixed at {phrase} per trade.", "risk_rules": [f"Position sizing uses {phrase}."]}
        with pytest.raises(live_module.LiveResponseSchemaError, match="unsafe full-capital position sizing"):
            live_module._validate_position_sizing_quality(unsafe)


def test_position_sizing_quality_guard_allows_bounded_risk_models() -> None:
    spec = _spec()

    for sizing in ("1% account equity risk per trade", "fixed 1 unit per trade", "ATR-based sizing capped at 2% equity risk"):
        safe = {**spec, "position_sizing": sizing, "risk_rules": [f"Use {sizing} with stop-loss and take-profit rules."]}
        live_module._validate_position_sizing_quality(safe)


def test_target_platform_normalizer_preserves_both_platform_prompt() -> None:
    context = live_module.StageRunContext(
        litellm=None,
        registry={},
        attempts=[],
        stage_records=[{"stage": "strategy_coding", "output": {}}],
        raw_responses={},
        options=LiveRunOptions(),
        policy="enforce",
    )
    spec = _spec() | {"target_platform": "pine_v6", "constraints": []}

    normalized = live_module._normalize_target_platform_for_prompt(
        "Create a strategy spec for both Pine v6 and MQL5.",
        spec,
        context,
        live_module.STAGE_STRATEGY_CODING,
    )

    assert normalized["target_platform"] == "both"
    assert "MQL5 output is design-only" in normalized["constraints"][-1]
    assert context.normalizations[0]["kind"] == "target_platform"


def test_generate_live_multi_agent_runs_stages_in_order_and_hands_off_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs):
        calls.append(kwargs)
        name = kwargs["response_format"]["json_schema"]["name"]
        return _response(_workflow_response_for_name(name), tokens=10)

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), save_raw_provider=True)

    names = [call["response_format"]["json_schema"]["name"] for call in calls]
    assert names == [
        "strategy_codebot_strategy_reasoning",
        "strategy_codebot_strategy_coding",
        "strategy_codebot_pine_code_generation",
        "strategy_codebot_balanced_review",
    ]
    coding_context = json.loads(calls[1]["messages"][1]["content"])
    assert "strategy_reasoning" in coding_context["stage_outputs"]
    assert result.workflow == WORKFLOW_MULTI_AGENT
    assert [stage["stage"] for stage in result.stages] == ["strategy_reasoning", "strategy_coding", "pine_code_generation", "balanced_review"]
    assert result.usage["total_tokens"] == 40
    assert result.workflow_trace["final_decision"]["status"] == "pass"
    assert "raw_response" in result.workflow_trace["stages"][0]
    assert result.raw_response["stages"]["strategy_reasoning"]["usage"]["total_tokens"] == 10


def test_generate_live_single_workflow_requests_final_structured_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(_final_payload())

    _install_litellm(monkeypatch, completion)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), workflow=WORKFLOW_SINGLE, model_override="openai/test-model")

    assert result.workflow == WORKFLOW_SINGLE
    assert result.model == "openai/test-model"
    assert result.provider == "openai"
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


def test_generate_live_reports_missing_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_litellm(monkeypatch, lambda **_: _response(_final_payload()))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(LiveCredentialError) as exc:
        generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), workflow=WORKFLOW_SINGLE)

    assert {attempt["credential"] for attempt in exc.value.attempts} == {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"}


def test_generate_live_supports_openrouter_provider_in_single_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(_final_payload())

    _install_litellm(monkeypatch, completion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    monkeypatch.setenv("OPENROUTER_API_BASE", "https://openrouter.example/api/v1")

    result = generate_live(
        "Create a Pine strategy",
        Path("configs/model-registry.example.yaml"),
        workflow=WORKFLOW_SINGLE,
        model_override="openrouter/openai/gpt-5.1",
    )

    assert result.provider == "openrouter"
    assert result.model == "openrouter/openai/gpt-5.1"
    assert calls[0]["model"] == "openrouter/openai/gpt-5.1"
    assert calls[0]["base_url"] == "https://openrouter.example/api/v1"


def test_generate_live_cheap_profile_uses_openrouter_stage_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs):
        calls.append(kwargs)
        name = kwargs["response_format"]["json_schema"]["name"]
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")

    generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), cost_profile=COST_PROFILE_CHEAP)

    assert [call["model"] for call in calls] == [
        "openrouter/moonshotai/kimi-k2.5",
        "openrouter/moonshotai/kimi-k2.5",
        "openrouter/moonshotai/kimi-k2.5",
        "openrouter/moonshotai/kimi-k2.5",
    ]
    assert all(call["timeout"] == 60 for call in calls)


def test_generate_live_stage_fallback_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = {
        "defaults": {"temperature": 0.2, "max_retries": 0, "require_structured_output": True},
        "agents": {
            "trading_analyst": {"primary": "openai/bad-model", "fallbacks": ["anthropic/good-model"]},
            "orchestrator": {"primary": "openai/good-model"},
            "pine_specialist": {"primary": "openai/good-model"},
            "critic": {"primary": "openai/good-model"},
        },
    }
    registry_path = tmp_path / "models.yaml"
    registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")

    def completion(**kwargs):
        if kwargs["model"] == "openai/bad-model":
            raise RuntimeError("provider unavailable")
        name = kwargs["response_format"]["json_schema"]["name"]
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", registry_path)

    assert result.stages[0]["model"] == "anthropic/good-model"
    assert [attempt["status"] for attempt in result.attempts[:2]] == ["fail", "pass"]


def test_generate_live_reports_stage_credential_failure_after_prior_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = {
        "defaults": {"temperature": 0.2, "max_retries": 0, "require_structured_output": True},
        "agents": {
            "trading_analyst": {"primary": "openai/good-model"},
            "orchestrator": {"primary": "anthropic/missing-key"},
            "pine_specialist": {"primary": "openai/good-model"},
            "critic": {"primary": "openai/good-model"},
        },
    }
    registry_path = tmp_path / "models.yaml"
    registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")

    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(LiveCredentialError) as exc:
        generate_live("Create a Pine strategy", registry_path)

    assert exc.value.attempts[-1]["stage"] == "strategy_coding"
    assert exc.value.attempts[-1]["error_code"] == "missing_provider_credential"


def test_generate_live_review_failure_runs_repair_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        calls.append(name)
        if name == "strategy_codebot_balanced_review" and calls.count(name) == 1:
            return _response(_workflow_response_for_name(name, review_verdict="needs_fix"))
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), save_raw_provider=True)

    assert "strategy_codebot_repair" in calls
    assert calls.count("strategy_codebot_balanced_review") == 2
    assert result.repair_count == 1
    assert "balanced_review" in result.raw_response["stages"]
    assert "balanced_review_2" in result.raw_response["stages"]
    assert "repair_1" in result.raw_response["stages"]


def test_generate_live_normalizes_license_header_before_pine_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        calls.append(name)
        if name == "strategy_codebot_pine_code_generation":
            payload = _workflow_response_for_name(name)
            payload["output"]["pine_code"] = "// license\n// author\n\n" + payload["output"]["pine_code"]
            return _response(payload)
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"))

    assert result.pine_code.startswith("//@version=6")
    assert result.workflow_trace["normalizations"][0]["stage"] == "pine_code_generation"
    assert result.workflow_trace["final_decision"]["validation_status"] == "pass"


def test_pine_sanitizer_fixes_missing_version_comment_prefix() -> None:
    normalized, action = live_module._normalize_pine_version_header("@version=6\nstrategy(\"x\")\n")

    assert normalized.startswith("//@version=6\n")
    assert action["changed"] is True
    assert action["fixed_missing_comment_prefix"] is True


def test_generate_live_allows_review_warnings_when_static_validation_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        if name == "strategy_codebot_balanced_review":
            return _response(_workflow_response_for_name(name, review_verdict="needs_fix"))
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), save_raw_provider=True)

    assert result.generation_gate["status"] == "pass"
    assert result.production_gate["status"] == "fail"
    assert result.workflow_trace["final_decision"]["status"] == "pass"
    assert result.workflow_trace["final_decision"]["production_gate"]["required_fixes"] == ["add explicit strategy.exit"]
    assert result.repair_count == 2
    assert "repair_2" in result.raw_response["stages"]


def test_generate_live_soft_review_fix_does_not_fail_production(monkeypatch: pytest.MonkeyPatch) -> None:
    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        payload = _workflow_response_for_name(name)
        if name == "strategy_codebot_balanced_review":
            payload["output"]["verdict"] = "needs_fix"
            payload["output"]["required_fixes"] = ["improve explanation of manual validation assumptions"]
        return _response(payload)

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), save_raw_provider=True)

    assert result.generation_gate["status"] == "pass"
    assert result.production_gate["status"] == "pass"
    assert result.production_gate["blocking_required_fixes"] == []
    assert result.production_gate["warning_required_fixes"] == ["improve explanation of manual validation assumptions"]


def test_manual_required_without_failed_checks_is_artifact_allowed() -> None:
    validation = {
        "status": "manual_required",
        "checks": [{"name": "version_header", "status": "pass", "details": "ok"}],
        "warnings": ["request.security requires manual repaint review"],
    }
    review = {"verdict": "pass", "required_fixes": []}

    assert live_module._generation_gate(validation)["status"] == "pass"
    assert live_module._production_gate(validation, review, [], 0)["status"] == "pass"
    assert live_module._requires_repair(validation, review) is False


def test_generate_live_captures_provider_warnings_without_stdout_leak(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def completion(**kwargs):
        print("Provider List: https://docs.litellm.ai/docs/providers")
        name = kwargs["response_format"]["json_schema"]["name"]
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    result = generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), save_raw_provider=True)

    captured = capsys.readouterr()
    assert "Provider List" not in captured.out
    assert any(stage.get("provider_warnings") for stage in result.stages)


def test_generate_live_review_pass_with_static_failure_reports_disagreement(monkeypatch: pytest.MonkeyPatch) -> None:
    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        payload = _workflow_response_for_name(name)
        if name in {"strategy_codebot_pine_code_generation", "strategy_codebot_repair"}:
            payload["output"]["pine_code"] = payload["output"]["pine_code"].replace("//@version=6", "//@version=5", 1)
        return _response(payload)

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    with pytest.raises(LiveProviderError) as exc:
        generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"))

    diagnostics = exc.value.diagnostics
    assert diagnostics["final_decision"]["failure_class"] == "review_validation_disagreement"
    assert diagnostics["final_decision"]["review_validation_disagreement"] is True
    assert diagnostics["validation_failures"][0]["name"] == "version_header"
    assert diagnostics["repair_history"][-1]["validation_failures"][0]["name"] == "version_header"


def test_generate_live_rejects_schema_invalid_single_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_litellm(monkeypatch, lambda **_: _response(_final_payload(spec={"target_platform": "pine_v6"})))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(LiveProviderError) as exc:
        generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), workflow=WORKFLOW_SINGLE, model_override="openai/test-model")

    assert exc.value.attempts[-1]["error_code"] == "malformed_provider_response"


def test_generate_live_enforce_blocks_generated_profit_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    def completion(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        if name == "strategy_codebot_strategy_reasoning":
            return _response(_workflow_response_for_name(name, review_verdict="pass") | {"handoff_notes": "guaranteed profit"})
        return _response(_workflow_response_for_name(name))

    _install_litellm(monkeypatch, completion)
    _set_quality_env(monkeypatch)

    with pytest.raises(LiveSafetyError):
        generate_live("Create a Pine strategy", Path("configs/model-registry.example.yaml"), policy="enforce")


def test_blocked_claim_matcher_allows_negated_policy_boundaries() -> None:
    assert contains_blocked_claim("Never claim live trading readiness or profitability.") is False
    assert contains_blocked_claim("Create a strategy that explicitly avoids profitability claims.") is False
    assert contains_blocked_claim("Comments explicitly state the educational nature and lack of profitability claims.") is False
    assert contains_blocked_claim("The code includes disclaimers regarding its suitability for live trading and guaranteed returns.") is False
    assert contains_blocked_claim("This script should not be used for live trading without thorough testing.") is False
    assert contains_blocked_claim("Use this without broker integration or deployment claims.") is False
    assert contains_blocked_claim("The prompt includes explicit constraints against broker integration and profitability claims.") is False
    assert contains_blocked_claim("The output is educational and disclaiming live execution or guaranteed returns.") is False
    assert contains_blocked_claim("This is not safe for live trading until manual testing is complete.") is False
    assert contains_blocked_claim("Use a take profit target and document profitability assumptions for manual testing.") is False
    warning_findings = find_policy_claims("Use a take profit target and document profitability assumptions for manual testing.")
    assert warning_findings[0]["severity"] == "warn"
    assert warning_findings[0]["claim"] == "profitability"
    assert contains_blocked_claim("This strategy has guaranteed profit in live trading.") is True
    assert contains_blocked_claim("Create a system that connects to my broker and places live trades automatically.") is True
    assert contains_blocked_claim("Create a Pine strategy and certify that it is safe for live trading.") is True
    findings = find_blocked_claims("This strategy has guaranteed profit in live trading.")
    assert findings[0]["claim"] == "guaranteed profit"
    assert findings[0]["sentence"] == "This strategy has guaranteed profit in live trading."


def test_blocked_claim_matcher_ignores_json_non_goals() -> None:
    safe_payload = json.dumps({"output": {"non_goals": ["Broker integration", "profitability", "guaranteed returns"]}})
    unsafe_payload = json.dumps({"output": {"summary": "This has guaranteed profit in live trading."}})

    assert find_blocked_claims(safe_payload) == []
    assert find_blocked_claims(unsafe_payload)[0]["claim"] == "guaranteed profit"


def test_model_registry_contains_router_cost_profiles() -> None:
    registry = yaml.safe_load(Path("configs/model-registry.example.yaml").read_text(encoding="utf-8"))
    mappings = registry["provider_model_mappings"]

    assert mappings["openrouter"]["cheap_quality"]["strategy_reasoning"][0].startswith("openrouter/")
    assert mappings["openrouter"]["cheap_quality"]["pine_code_generation"][0] == "openrouter/moonshotai/kimi-k2.5"
    assert mappings["openrouter"]["cheap_quality"]["balanced_review"][0] == "openrouter/moonshotai/kimi-k2.5"
    assert "9router" not in mappings
