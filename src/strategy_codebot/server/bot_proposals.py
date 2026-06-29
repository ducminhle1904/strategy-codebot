from dataclasses import dataclass, field
from typing import Any

from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_MISSING_INPUTS
from strategy_codebot.server.bot_proposal_status import BOT_PROPOSAL_STATUS_READY
from strategy_codebot.server.repository import BotProposalCreateInput
from strategy_codebot.server.repository import ConversationRepository


class BotProposalSourceNotFoundError(Exception):
    def __init__(self, source: str) -> None:
        super().__init__(source)
        self.source = source


class BotProposalArtifactUnreadableError(Exception):
    pass


@dataclass(frozen=True)
class BotProposalDraftInput:
    strategy_artifact_id: str | None = None
    run_id: str | None = None
    fallback_run_id: str | None = None
    fallback_conversation_id: str | None = None
    strategy_spec: dict[str, Any] | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    data_subscriptions: list[dict[str, Any]] = field(default_factory=list)
    broker_connection_id: str | None = None
    account_id: str | None = None
    risk_policy_id: str | None = None
    readiness_checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BotProposalDraftResult:
    create_input: BotProposalCreateInput
    missing_inputs: list[str]


def build_bot_proposal_create_input(
    *,
    auth: AuthContext,
    repository: ConversationRepository,
    artifact_store: LocalArtifactStore,
    draft: BotProposalDraftInput,
) -> BotProposalDraftResult:
    source_artifact_ids: list[str] = []
    source_run_id = _clean_string(draft.run_id) or _clean_string(draft.fallback_run_id)
    source_conversation_id = _clean_string(draft.fallback_conversation_id)
    strategy_spec = dict(draft.strategy_spec) if isinstance(draft.strategy_spec, dict) else None
    manifest = dict(draft.manifest)
    if strategy_spec is not None:
        manifest = {**strategy_spec, **manifest}

    if draft.strategy_artifact_id:
        artifact = repository.get_artifact(auth, draft.strategy_artifact_id)
        if artifact is None:
            raise BotProposalSourceNotFoundError("strategy_artifact")
        source_artifact_ids.append(artifact.id)
        source_run_id = source_run_id or artifact.run_id
        source_conversation_id = artifact.conversation_id or source_conversation_id
        try:
            artifact_payload = artifact_store.read_content(artifact)
        except Exception as exc:
            raise BotProposalArtifactUnreadableError("Could not read strategy artifact") from exc
        if isinstance(artifact_payload, dict):
            strategy_spec = (
                artifact_payload["strategy_spec"]
                if isinstance(artifact_payload.get("strategy_spec"), dict)
                else artifact_payload
            )
            manifest = {**strategy_spec, **manifest}

    if source_run_id:
        spec_record = repository.get_strategy_spec_for_run(auth, source_run_id) if strategy_spec is None else None
        if spec_record is not None:
            strategy_spec = spec_record.payload_json
            manifest = {**strategy_spec, **manifest}
        if source_conversation_id is None:
            run = repository.get_run(auth, source_run_id)
            if run is not None:
                source_conversation_id = run.conversation_id
        if strategy_spec is None and draft.run_id:
            raise BotProposalSourceNotFoundError("strategy_run")

    data_subscriptions = [item for item in draft.data_subscriptions if isinstance(item, dict)]
    if not data_subscriptions:
        raw_subscriptions = manifest.get("data_subscriptions")
        if isinstance(raw_subscriptions, list):
            data_subscriptions = [item for item in raw_subscriptions if isinstance(item, dict)]
    if not data_subscriptions:
        data_subscriptions = _subscription_from_strategy_spec(strategy_spec)

    strategy_id = (
        _clean_string(draft.strategy_id)
        or _safe_manifest_string(manifest, "strategy_id")
        or source_run_id
        or _clean_string(draft.strategy_artifact_id)
    )
    strategy_name = (
        _clean_string(draft.strategy_name)
        or _safe_manifest_string(manifest, "name")
        or _strategy_name_from_spec(strategy_spec, strategy_id or "Bot")
    )
    if strategy_id:
        manifest.setdefault("strategy_id", strategy_id)
    manifest.setdefault("name", strategy_name)
    if strategy_spec is not None:
        manifest.setdefault("strategy_spec", strategy_spec)

    readiness_checks = list(draft.readiness_checks)
    if source_artifact_ids or source_run_id:
        readiness_checks.append("Strategy source linked")
    readiness_checks.append("No broker execution")
    missing_inputs = bot_required_missing(
        broker_connection_id=draft.broker_connection_id,
        account_id=draft.account_id,
        risk_policy_id=draft.risk_policy_id,
        strategy_id=strategy_id,
        data_subscriptions=data_subscriptions,
    )
    create_input = BotProposalCreateInput(
        status=BOT_PROPOSAL_STATUS_MISSING_INPUTS if missing_inputs else BOT_PROPOSAL_STATUS_READY,
        source_conversation_id=source_conversation_id,
        source_run_id=source_run_id,
        source_artifact_ids=source_artifact_ids,
        strategy_id=strategy_id or "bot_strategy",
        strategy_name=strategy_name or "Bot",
        manifest_json=manifest,
        data_subscriptions_json=data_subscriptions,
        broker_connection_id=draft.broker_connection_id,
        account_id=draft.account_id,
        risk_policy_id=draft.risk_policy_id,
        readiness_checks_json=readiness_checks,
        missing_inputs_json=missing_inputs,
    )
    return BotProposalDraftResult(create_input=create_input, missing_inputs=missing_inputs)


def bot_required_missing(
    *,
    broker_connection_id: str | None,
    account_id: str | None,
    risk_policy_id: str | None,
    strategy_id: str | None,
    data_subscriptions: list,
) -> list[str]:
    missing: list[str] = []
    for key, value in (
        ("broker_connection_id", broker_connection_id),
        ("account_id", account_id),
        ("risk_policy_id", risk_policy_id),
        ("strategy_id", strategy_id),
    ):
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    if not data_subscriptions:
        missing.append("data_subscriptions")
    return missing


def _subscription_from_strategy_spec(strategy_spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(strategy_spec, dict):
        return []
    symbol = strategy_spec.get("symbol")
    timeframe = strategy_spec.get("timeframe")
    if not isinstance(symbol, str) or not symbol.strip() or not isinstance(timeframe, str) or not timeframe.strip():
        return []
    subscription: dict[str, Any] = {"symbol": symbol.strip(), "timeframe": timeframe.strip()}
    market = strategy_spec.get("market")
    if isinstance(market, str) and market.strip():
        subscription["market"] = market.strip()
    return [subscription]


def _strategy_name_from_spec(strategy_spec: dict[str, Any] | None, fallback: str) -> str:
    if isinstance(strategy_spec, dict):
        for key in ("name", "strategy_name", "title"):
            value = strategy_spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
        symbol = strategy_spec.get("symbol")
        timeframe = strategy_spec.get("timeframe")
        if isinstance(symbol, str) and symbol.strip() and isinstance(timeframe, str) and timeframe.strip():
            return f"{symbol.strip()} {timeframe.strip()} Bot"[:240]
    return fallback[:240]


def _safe_manifest_string(manifest: dict[str, Any], key: str) -> str | None:
    value = manifest.get(key)
    return _clean_string(value)


def _clean_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
