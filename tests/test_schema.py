import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import ValidationError

from strategy_codebot.schemas import load_json, load_strategy_spec, load_strategy_spec_model, validate_payload


def test_strategy_spec_example_is_valid() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    assert spec["target_platform"] == "pine_v6"
    assert spec["script_type"] == "strategy"


def test_strategy_spec_model_round_trips_existing_example() -> None:
    spec = load_strategy_spec_model(Path("examples/specs/ma-crossover-pine.json"))

    assert spec.target_platform == "pine_v6"
    assert spec.script_type == "strategy"
    assert spec.entry_rules


def test_nautilus_strategy_spec_example_is_valid() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-nautilus.json"))

    assert spec["target_platform"] == "nautilus_py"
    assert spec["venue"] == "BINANCE"


def test_strategy_spec_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        validate_payload({"target_platform": "pine_v6"}, "strategy-spec.schema.json")


def test_review_report_schema_file_is_valid_json_schema() -> None:
    schema = load_json(Path("schemas/review-report.schema.json"))

    assert schema["title"] == "ReviewReport"


def test_parity_report_schema_accepts_minimal_manual_required_report() -> None:
    payload = {
        "kind": "parity_report",
        "created_at": "2026-06-26T00:00:00Z",
        "status": "manual_required",
        "target_platform": "nautilus_py",
        "reference_runtime": "spec_oracle",
        "compared_runtime": "nautilus_py",
        "checks": [{"name": "signal_trace", "status": "manual_required"}],
        "evidence": ["fixture"],
        "warnings": ["manual proof required"],
        "next_actions": ["Compare trace output."],
    }

    validate_payload(payload, "parity-report.schema.json")


def test_parity_report_rejects_unknown_check_status() -> None:
    payload = {
        "kind": "parity_report",
        "created_at": "2026-06-26T00:00:00Z",
        "status": "pass",
        "target_platform": "nautilus_py",
        "reference_runtime": "spec_oracle",
        "compared_runtime": "nautilus_py",
        "checks": [{"name": "signal_trace", "status": "unknown"}],
        "evidence": [],
        "warnings": [],
        "next_actions": [],
    }

    with pytest.raises(ValidationError):
        validate_payload(payload, "parity-report.schema.json")


def test_package_metadata_is_product_ready() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    metadata = pyproject["project"]
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert metadata["license"]["file"] == "LICENSE"
    assert metadata["authors"]
    assert "strategy-codebot" in metadata["urls"]["Repository"]
    assert "License :: OSI Approved :: MIT License" in metadata["classifiers"]
    assert force_include["configs"] == "configs"
    assert force_include["schemas"] == "schemas"
    assert force_include["examples"] == "examples"
