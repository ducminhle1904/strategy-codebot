from __future__ import annotations

import json
from typing import Any


PROMPT_PROFILE_CURRENT = "current"
PROMPT_PROFILE_OPTIMIZED_V1 = "optimized_v1"
PROMPT_PROFILES = {PROMPT_PROFILE_CURRENT, PROMPT_PROFILE_OPTIMIZED_V1}
DEFAULT_PROMPT_PROFILE = PROMPT_PROFILE_CURRENT
DEFAULT_PROMPT_MATRIX_PROFILES = (PROMPT_PROFILE_CURRENT, PROMPT_PROFILE_OPTIMIZED_V1)

STAGE_STRATEGY_REASONING = "strategy_reasoning"
STAGE_STRATEGY_CODING = "strategy_coding"
STAGE_PINE_CODE_GENERATION = "pine_code_generation"
STAGE_BALANCED_REVIEW = "balanced_review"
STAGE_REPAIR = "repair"
WORKFLOW_STAGES = (STAGE_STRATEGY_REASONING, STAGE_STRATEGY_CODING, STAGE_PINE_CODE_GENERATION, STAGE_BALANCED_REVIEW)
MODEL_STAGE_KEYS = {*WORKFLOW_STAGES, STAGE_REPAIR}

SHARED_SAFETY_BOUNDARY = (
    "Return only JSON matching the provided schema. "
    "Never claim profitability, live-trading readiness, live execution readiness, broker integration, broker deployment, "
    "runtime validation, guaranteed returns, or risk-free behavior. "
    "Take-profit and profit-target rules are allowed as strategy mechanics, not performance claims."
)

CURRENT_STAGE_PROMPT_TEMPLATES = {
    STAGE_STRATEGY_REASONING: (
        "Analyze the trading prompt and create a strategy brief. Do not write Pine code. "
        "Include a concrete market premise/regime, failure mode, invalidation, timeframe/session or liquidity assumptions, and ask-before-coding gaps. "
        "For strategy outputs, include concrete position sizing, stop-loss, and take-profit assumptions "
        "unless the prompt explicitly requests indicator-only output or excludes fixed risk exits. "
        "State risk concentration assumptions: per-trade risk, exposure or portfolio heat, correlated-position cap, and leverage boundary. "
        "If the prompt asks for price-action-only or no-indicator logic, do not add ATR, moving averages, RSI, MACD, stochastic, or other indicators; use explicit OHLC swing, wick, close, sweep, reclaim, BOS/retest, and structure rules. "
        "For sweep/reclaim logic, describe failed-reclaim handling and avoid chasing every break. "
        "{conservative_sizing_guidance}"
    ),
    STAGE_STRATEGY_CODING: (
        "Convert the strategy brief into a valid strategy_spec JSON object. Do not write Pine code. "
        "Encode the trading thesis, regime, invalidation, session/liquidity assumptions, and false-break handling into rules or constraints. "
        "Populate stop_loss and take_profit for strategy outputs when risk exits are expected. "
        "Set position_sizing to fixed units, 1-2% account equity risk, or another explicitly bounded small-risk model; never use all-in or full-capital sizing. "
        "Add risk_rules for single-strategy exposure, portfolio-heat or correlated-position caps, and no leverage unless explicitly bounded. "
        "For price-action-only or no-indicator prompts, encode indicator bans in constraints and use only OHLC-derived swing/structure rules. "
        "{conservative_sizing_guidance}"
    ),
    STAGE_PINE_CODE_GENERATION: (
        "Generate Pine Script v6 from the supplied strategy_spec without changing strategy logic. "
        "For strategy entries with stop-loss or take-profit risk rules, implement exits with "
        "strategy.exit using stop and/or limit parameters; do not rely only on strategy.close. "
        "Implement clear entry, invalidation, stop, and target mechanics from the spec; do not add unrequested indicators to fill missing logic. "
        "If strategy_spec forbids indicators for price-action-only logic, do not use ta.atr, moving averages, RSI, MACD, stochastic, or oscillator helpers."
    ),
    STAGE_BALANCED_REVIEW: (
        "Review the full context for schema, Pine, trading-logic, and safety issues. If static "
        "validation has failing checks, verdict must be needs_fix or fail with required fixes. "
        "Also assess trader-grade completeness: premise/regime, invalidation, false-break handling, session/liquidity assumptions, and overfit risk. "
        "Manual-required warnings may pass only when they are explained and non-blocking."
    ),
    STAGE_REPAIR: (
        "Repair all static validation failures first, especially "
        "version_header, repaint_hazards such as barmerge.lookahead_on, and missing strategy.exit "
        "for stop-loss/take-profit behavior, then review findings. For repaint_hazards, remove "
        "barmerge.lookahead_on and use barmerge.lookahead_off or confirmed-bar logic. Preserve the accepted strategy intent. "
        "For price-action-only repairs, remove forbidden indicator helpers instead of broadening the strategy logic; preserve sweep/reclaim invalidation, failed-reclaim avoidance, and structure target or bounded reward/risk fallback. "
        "{conservative_sizing_guidance}"
    ),
}

OPTIMIZED_STAGE_PROMPT_TEMPLATES = {
    STAGE_STRATEGY_REASONING: (
        "Create a concise strategy brief, not Pine code. State the market thesis first: regime, timeframe/session, liquidity condition, and when the setup should be avoided. "
        "Then specify entry trigger, invalidation, failed-break/fakeout handling, stop/target premise, bounded sizing, and ask-before-coding gaps. "
        "Include risk concentration assumptions: per-trade risk, exposure/portfolio heat, correlation, and leverage boundary. "
        "For price-action-only prompts, use OHLC structure terms only: sweep, reclaim, wick, close, BOS/retest, prior swing, support/resistance. "
        "Do not introduce ATR, MA, RSI, MACD, stochastic, oscillators, or other indicators when the prompt forbids indicators. "
        "{conservative_sizing_guidance}"
    ),
    STAGE_STRATEGY_CODING: (
        "Convert the brief into strategy_spec only. Encode premise/regime, avoid conditions, entry trigger, invalidation, stop, target, session/timeframe, false-break handling, and indicator bans as explicit fields or constraints. "
        "Use fixed units, 1-2% account equity risk, or another bounded small-risk sizing model; never full balance, all-in, or 100% equity. "
        "Risk rules must state exposure or portfolio heat assumptions, correlated-position cap, and leverage boundary. "
        "If information is missing, encode conservative assumptions rather than inventing extra indicators. "
        "{conservative_sizing_guidance}"
    ),
    STAGE_PINE_CODE_GENERATION: (
        "Implement Pine Script v6 from strategy_spec only. Preserve strategy logic exactly; do not add unrequested filters, indicators, or optimization knobs. "
        "Implement entry, invalidation, stop, target, and failed-break avoidance explicitly. Use strategy.exit stop/limit mechanics for stop-loss/take-profit behavior. "
        "If strategy_spec forbids indicators, avoid ta.atr, MA helpers, RSI, MACD, stochastic, oscillators, and volume indicators."
    ),
    STAGE_BALANCED_REVIEW: (
        "Review static correctness first: schema, Pine v6, strategy/spec alignment, strategy.exit risk exits, repaint hazards, and forbidden indicator drift. "
        "If static validation fails, verdict must be needs_fix or fail. Then assess trader-grade completeness as soft quality evidence: premise/regime, invalidation, avoid conditions, session/liquidity assumptions, false-break handling, bounded risk, and overfit risk. "
        "Put non-blocking sophistication gaps in required_fixes only as warnings; do not override passing static validation unless the soft quality gate is weak."
    ),
    STAGE_REPAIR: (
        "Repair with the smallest change that clears deterministic validation. Fix version header, schema mismatch, repaint hazards, missing strategy.exit, forbidden indicators, and unsafe sizing before style. "
        "Preserve accepted strategy intent, premise, invalidation, and price-action constraints. For price-action-only logic, remove forbidden helpers instead of broadening the strategy. "
        "{conservative_sizing_guidance}"
    ),
}

_STAGE_PROMPT_TEMPLATES_BY_PROFILE = {
    PROMPT_PROFILE_CURRENT: CURRENT_STAGE_PROMPT_TEMPLATES,
    PROMPT_PROFILE_OPTIMIZED_V1: OPTIMIZED_STAGE_PROMPT_TEMPLATES,
}


def normalize_prompt_profile(profile: str) -> str:
    normalized = str(profile or "").strip()
    if normalized not in PROMPT_PROFILES:
        raise ValueError("prompt_profile must be current or optimized_v1")
    return normalized


def normalize_prompt_profiles(profiles: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        value = normalize_prompt_profile(profile)
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    if not normalized:
        raise ValueError("profiles must contain at least one prompt profile")
    return normalized


def compact_free_messages(
    prompt: str,
    compact_context: dict[str, Any],
    *,
    conservative_sizing_guidance: str,
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
) -> list[dict[str, str]]:
    prompt_profile = normalize_prompt_profile(prompt_profile)
    if prompt_profile == PROMPT_PROFILE_OPTIMIZED_V1:
        system = (
            "You generate reviewable Pine Script v6 strategy artifacts for a compact free-tier workflow. "
            "Keep logic simple, deterministic, and static-validation friendly. "
            f"{conservative_sizing_guidance} "
            "Use explicit stop-loss and take-profit mechanics when producing a strategy. "
            f"{SHARED_SAFETY_BOUNDARY}"
        )
        user = {
            "original_prompt": prompt,
            "knowledge_context": compact_context,
            "contract": {
                "required_outputs": ["strategy_spec", "pine_code"],
                "pine": ["start with //@version=6", "use strategy(...) for strategies", "use strategy.exit(...) for stop/target exits"],
                "strategy_spec": ["bounded position_sizing", "risk_rules include stop loss and take profit assumptions"],
            },
        }
        return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)}]
    return [
        {
            "role": "system",
            "content": (
                "You generate reviewable Pine Script v6 strategy artifacts for a free-tier compact workflow. "
                "Keep the logic simple, deterministic, and validation-friendly. "
                f"{conservative_sizing_guidance} "
                f"{SHARED_SAFETY_BOUNDARY}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original prompt:\n{prompt}\n\n"
                f"Knowledge context:\n{json.dumps(compact_context, ensure_ascii=False)}\n\n"
                "Produce strategy_spec and pine_code. Pine must start with //@version=6. "
                "For Pine strategies, strategy_spec must include conservative position_sizing and risk_rules that explicitly mention stop loss and take profit assumptions."
            ),
        },
    ]


def compact_free_repair_messages(
    prompt: str,
    repair_payload: dict[str, Any],
    *,
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
) -> list[dict[str, str]]:
    prompt_profile = normalize_prompt_profile(prompt_profile)
    if prompt_profile == PROMPT_PROFILE_OPTIMIZED_V1:
        system = (
            "Repair a compact free-tier Pine Script v6 strategy artifact with the smallest validation-focused change. "
            "Prioritize static validation failures before style. Preserve the strategy intent and risk assumptions. "
            f"{SHARED_SAFETY_BOUNDARY}"
        )
        user = {
            "original_prompt": prompt,
            "repair_input": repair_payload,
            "contract": {
                "required_outputs": ["strategy_spec", "pine_code"],
                "pine": ["//@version=6", "strategy(...)", "strategy.exit(...) for stop/target behavior"],
                "repaint": "avoid request.security unless required; if used, set lookahead=barmerge.lookahead_off",
            },
        }
        return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)}]
    return [
        {
            "role": "system",
            "content": (
                "Repair a failed free-tier Pine Script v6 strategy artifact. Return strict JSON only matching the response schema. "
                "Prioritize static validation failures before style. Pine must include //@version=6, strategy(...), and strategy.exit(...) "
                "for stop-loss/take-profit behavior. strategy_spec must include conservative position_sizing and risk_rules with stop loss and take profit assumptions. "
                "Avoid request.security unless the prompt requires it; if request.security is used, include lookahead=barmerge.lookahead_off explicitly. "
                f"{SHARED_SAFETY_BOUNDARY}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original prompt:\n{prompt}\n\n"
                f"Repair input:\n{json.dumps(repair_payload, ensure_ascii=False)}\n\n"
                "Return corrected strategy_spec and pine_code only."
            ),
        },
    ]


def single_workflow_messages(prompt: str, compact_context: dict[str, Any] | None, *, prompt_profile: str = DEFAULT_PROMPT_PROFILE) -> list[dict[str, str]]:
    prompt_profile = normalize_prompt_profile(prompt_profile)
    if compact_context:
        user_payload: str | dict[str, Any] = {"original_prompt": prompt, "knowledge_context": compact_context}
    else:
        user_payload = prompt
    if prompt_profile == PROMPT_PROFILE_OPTIMIZED_V1:
        system = (
            "Generate one reviewable Pine Script v6 strategy artifact. "
            "Use bounded risk assumptions, explicit invalidation, and strategy.exit stop/target mechanics when a strategy is requested. "
            f"{SHARED_SAFETY_BOUNDARY}"
        )
        if isinstance(user_payload, dict):
            user_payload = {
                **user_payload,
                "contract": {
                    "required_outputs": ["strategy_spec", "pine_code"],
                    "avoid": ["unrequested indicators", "full-capital sizing", "profitability claims"],
                },
            }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True) if isinstance(user_payload, dict) else user_payload},
        ]
    return [
        {
            "role": "system",
            "content": (
                "Generate reviewable Pine Script v6 artifacts only. "
                f"{SHARED_SAFETY_BOUNDARY}"
            ),
        },
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True) if isinstance(user_payload, dict) else user_payload},
    ]


def stage_messages(
    stage: str,
    context_packet: dict[str, Any],
    *,
    conservative_sizing_guidance: str,
    repair_iteration: int | None,
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
) -> list[dict[str, str]]:
    prompt_profile = normalize_prompt_profile(prompt_profile)
    role_prompt = _optimized_stage_prompt(stage, conservative_sizing_guidance) if prompt_profile == PROMPT_PROFILE_OPTIMIZED_V1 else _current_stage_prompt(stage, conservative_sizing_guidance)
    repair_note = f" Repair iteration {repair_iteration}." if repair_iteration else ""
    content = (
        f"You are the {stage} stage in a multi-model strategy generation workflow. "
        f"{role_prompt}{repair_note} {SHARED_SAFETY_BOUNDARY}"
    )
    return [
        {"role": "system", "content": content},
        {"role": "user", "content": json.dumps(context_packet, ensure_ascii=False, sort_keys=True)},
    ]


def _current_stage_prompt(stage: str, conservative_sizing_guidance: str) -> str:
    return _stage_prompt_from_templates(stage, CURRENT_STAGE_PROMPT_TEMPLATES, conservative_sizing_guidance=conservative_sizing_guidance)


def _optimized_stage_prompt(stage: str, conservative_sizing_guidance: str) -> str:
    return _stage_prompt_from_templates(stage, OPTIMIZED_STAGE_PROMPT_TEMPLATES, conservative_sizing_guidance=conservative_sizing_guidance)


def _stage_prompt_from_templates(stage: str, templates: dict[str, str], *, conservative_sizing_guidance: str) -> str:
    try:
        template = templates[stage]
    except KeyError as exc:
        raise ValueError(f"unknown strategy workflow stage: {stage}") from exc
    return template.format(conservative_sizing_guidance=conservative_sizing_guidance)


def _validate_stage_prompt_template_coverage() -> None:
    for profile, templates in _STAGE_PROMPT_TEMPLATES_BY_PROFILE.items():
        missing = sorted(MODEL_STAGE_KEYS - set(templates))
        extra = sorted(set(templates) - MODEL_STAGE_KEYS)
        if missing or extra:
            raise ValueError(f"{profile} prompt template coverage mismatch: missing={missing} extra={extra}")


_validate_stage_prompt_template_coverage()
