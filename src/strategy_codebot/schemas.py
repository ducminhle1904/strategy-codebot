from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from strategy_codebot.paths import ensure_parent
from strategy_codebot.paths import repo_root
from strategy_codebot.strategy_spec import StrategySpec
from strategy_codebot.strategy_spec import parse_strategy_spec


SCHEMA_DIR = repo_root() / "schemas"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def schema(name: str) -> dict[str, Any]:
    return load_json(SCHEMA_DIR / name)


@lru_cache(maxsize=None)
def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(schema(schema_name))


def validate_payload(payload: dict[str, Any], schema_name: str) -> None:
    validator(schema_name).validate(payload)


def load_strategy_spec(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    validate_payload(payload, "strategy-spec.schema.json")
    return payload


def load_strategy_spec_model(path: Path) -> StrategySpec:
    return parse_strategy_spec(load_strategy_spec(path))
