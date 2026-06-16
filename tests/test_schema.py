import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from strategy_codebot.schemas import load_strategy_spec, validate_payload


def test_strategy_spec_example_is_valid() -> None:
    spec = load_strategy_spec(Path("examples/specs/ma-crossover-pine.json"))

    assert spec["target_platform"] == "pine_v6"
    assert spec["script_type"] == "strategy"


def test_strategy_spec_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        validate_payload({"target_platform": "pine_v6"}, "strategy-spec.schema.json")

