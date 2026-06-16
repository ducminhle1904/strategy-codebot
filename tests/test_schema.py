import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import ValidationError

from strategy_codebot.schemas import load_json, load_strategy_spec, validate_payload


def test_strategy_spec_example_is_valid() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    assert spec["target_platform"] == "pine_v6"
    assert spec["script_type"] == "strategy"


def test_strategy_spec_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        validate_payload({"target_platform": "pine_v6"}, "strategy-spec.schema.json")


def test_review_report_schema_file_is_valid_json_schema() -> None:
    schema = load_json(Path("schemas/review-report.schema.json"))

    assert schema["title"] == "ReviewReport"


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
