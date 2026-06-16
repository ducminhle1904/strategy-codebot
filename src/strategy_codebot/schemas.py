from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from strategy_codebot.paths import repo_root


SCHEMA_DIR = repo_root() / "schemas"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def schema(name: str) -> dict[str, Any]:
    return load_json(SCHEMA_DIR / name)


def validate_payload(payload: dict[str, Any], schema_name: str) -> None:
    Draft202012Validator(schema(schema_name)).validate(payload)


def load_strategy_spec(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    validate_payload(payload, "strategy-spec.schema.json")
    return payload

