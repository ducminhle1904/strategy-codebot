import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def sync_workflow_registry_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sync_workflow_registry",
        ROOT / "scripts" / "sync-workflow-registry.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_registry_contract() -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "workflow-registry.json").read_text(encoding="utf-8"))


def test_workflow_registry_validator_rejects_duplicate_step_ids() -> None:
    module = sync_workflow_registry_module()
    contract = workflow_registry_contract()
    workflow = contract["workflows"]["strategy_bot_simulation"]
    workflow["steps"].append(copy.deepcopy(workflow["steps"][0]))

    with pytest.raises(SystemExit, match="steps must not contain duplicates"):
        module.validate_contract(contract, source="test-contract")


def test_workflow_registry_validator_rejects_unknown_component_kind() -> None:
    module = sync_workflow_registry_module()
    contract = workflow_registry_contract()
    workflow = contract["workflows"]["strategy_bot_simulation"]
    workflow["sections"][0]["component_kind"] = "unknown_component"

    with pytest.raises(SystemExit, match="component_kind is not allowed"):
        module.validate_contract(contract, source="test-contract")


def test_workflow_registry_validator_rejects_invalid_action_gate_ref() -> None:
    module = sync_workflow_registry_module()
    contract = workflow_registry_contract()
    workflow = contract["workflows"]["strategy_bot_simulation"]
    workflow["sections"][1]["action_id"] = "unknown_action"

    with pytest.raises(SystemExit, match="action_id is unknown"):
        module.validate_contract(contract, source="test-contract")
