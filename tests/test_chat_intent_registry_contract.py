import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def sync_chat_intent_registry_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sync_chat_intent_registry",
        ROOT / "scripts" / "sync-chat-intent-registry.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chat_intent_contract() -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "chat-intent-registry.json").read_text(encoding="utf-8"))


def test_chat_intent_registry_contract_generated_files_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/sync-chat-intent-registry.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_chat_intent_registry_validator_accepts_valid_registry() -> None:
    module = sync_chat_intent_registry_module()

    module.validate_contract(chat_intent_contract(), source="test-contract")


def test_chat_intent_registry_validator_rejects_duplicate_ids() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["response_intents"].append(contract["response_intents"][0])

    with pytest.raises(SystemExit, match="response_intents contains duplicate ids"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_missing_policy_coverage() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["response_intent_policies"].pop("general_chat")

    with pytest.raises(SystemExit, match="response_intent_policies must cover every response intent"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_unknown_workflow_ref() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["response_intent_policies"]["general_chat"]["allowed_workflow_intents"] = ["unknown_workflow"]

    with pytest.raises(SystemExit, match="unknown workflow intents"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_unknown_stage_ref() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["intent_model_stages"]["general_chat"] = "unknown_stage"

    with pytest.raises(SystemExit, match="unknown stage refs"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_invalid_fallback_policy() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["classifier_fallback_policy"]["safe_workflow_kickoff_allowed"] = "yes"

    with pytest.raises(SystemExit, match="safe_workflow_kickoff_allowed must be boolean"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_invalid_workflow_timeout_fallback_ref() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["workflow_intent_policies"]["strategy_to_paper_bot_simulation"]["timeout_fallback"][
        "denied_evidence_signals"
    ] = ["unknown_signal"]

    with pytest.raises(SystemExit, match="timeout_fallback.denied_evidence_signals"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_invalid_ui_policy() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["response_intent_ui_policies"]["strategy_building"]["show_strategy_profile"] = "yes"

    with pytest.raises(SystemExit, match="show_strategy_profile must be boolean"):
        module.validate_contract(contract, source="test-contract")


def test_chat_intent_registry_validator_rejects_missing_ui_policy_coverage() -> None:
    module = sync_chat_intent_registry_module()
    contract = chat_intent_contract()
    contract["response_intent_ui_policies"].pop("general_chat")

    with pytest.raises(SystemExit, match="response_intent_ui_policies must cover every response intent"):
        module.validate_contract(contract, source="test-contract")
