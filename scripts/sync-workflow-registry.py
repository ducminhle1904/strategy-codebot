#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "workflow-registry.json"


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract, source=path)
    return contract


def validate_contract(contract: dict[str, Any], *, source: Path | str = CONTRACT_PATH) -> None:
    label = str(source)
    if contract.get("schema_version") != 1:
        raise SystemExit(f"{label} schema_version must be 1")
    component_kinds = contract.get("component_kinds")
    if not _string_list(component_kinds) or len(set(component_kinds)) != len(component_kinds):
        raise SystemExit(f"{label} component_kinds must be a unique non-empty string array")
    workflows = contract.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise SystemExit(f"{label} workflows must be a non-empty object")
    for workflow_id, workflow in workflows.items():
        _validate_workflow(label, workflow_id, workflow, set(component_kinds))


def _validate_workflow(
    label: str,
    workflow_id: object,
    workflow: object,
    component_kinds: set[str],
) -> None:
    if not isinstance(workflow_id, str) or not workflow_id:
        raise SystemExit(f"{label} workflow ids must be non-empty strings")
    if not isinstance(workflow, dict):
        raise SystemExit(f"{label} workflow {workflow_id} must be an object")
    for key in ("intent", "title", "aria_label", "icon_key", "default_status"):
        if not isinstance(workflow.get(key), str) or not workflow[key]:
            raise SystemExit(f"{label} workflow {workflow_id} {key} must be a non-empty string")
    if workflow["icon_key"] not in {"bot", "checklist"}:
        raise SystemExit(f"{label} workflow {workflow_id} icon_key is not supported")
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SystemExit(f"{label} workflow {workflow_id} steps must be a non-empty array")
    step_ids = [_step_id(label, workflow_id, step) for step in steps]
    _ensure_unique(label, f"workflow {workflow_id} steps", step_ids)
    allowed_fields = _workflow_allowed_fields(label, workflow_id, workflow)
    _workflow_required_input_fields(label, workflow_id, workflow)
    allowed_section_kinds = workflow.get("allowed_section_kinds")
    if not _string_list(allowed_section_kinds):
        raise SystemExit(f"{label} workflow {workflow_id} allowed_section_kinds must be a non-empty string array")
    if any(kind not in component_kinds for kind in allowed_section_kinds):
        raise SystemExit(f"{label} workflow {workflow_id} allowed_section_kinds contains unknown component kind")
    statuses = workflow.get("status_labels")
    if not isinstance(statuses, dict) or not statuses:
        raise SystemExit(f"{label} workflow {workflow_id} status_labels must be a non-empty object")
    if workflow["default_status"] not in statuses:
        raise SystemExit(f"{label} workflow {workflow_id} default_status must exist in status_labels")
    for status_key, status in statuses.items():
        if not isinstance(status, dict) or status.get("key") != status_key:
            raise SystemExit(f"{label} workflow {workflow_id} status {status_key} key must match object key")
        if not isinstance(status.get("label"), str) or not status["label"]:
            raise SystemExit(f"{label} workflow {workflow_id} status {status_key} label is required")
        if status.get("tone") not in {"neutral", "success", "warning", "danger"}:
            raise SystemExit(f"{label} workflow {workflow_id} status {status_key} tone is invalid")
    actions = workflow.get("actions", [])
    if not isinstance(actions, list):
        raise SystemExit(f"{label} workflow {workflow_id} actions must be an array")
    action_ids = [_action_id(label, workflow_id, action) for action in actions]
    _ensure_unique(label, f"workflow {workflow_id} actions", action_ids)
    sections = workflow.get("sections")
    if not isinstance(sections, list):
        raise SystemExit(f"{label} workflow {workflow_id} sections must be an array")
    section_ids = [_section_id(label, workflow_id, section) for section in sections]
    _ensure_unique(label, f"workflow {workflow_id} sections", section_ids)
    for section in sections:
        _validate_section(
            label,
            workflow_id,
            workflow,
            section,
            allowed_fields=set(allowed_fields),
            allowed_section_kinds=set(allowed_section_kinds),
            action_ids=set(action_ids),
        )
    for key in ("badges", "model_guidance"):
        if not _string_list(workflow.get(key)):
            raise SystemExit(f"{label} workflow {workflow_id} {key} must be a non-empty string array")


def _step_id(label: str, workflow_id: str, step: object) -> str:
    if not isinstance(step, dict):
        raise SystemExit(f"{label} workflow {workflow_id} steps must contain objects")
    step_id = step.get("id")
    if not isinstance(step_id, str) or not step_id:
        raise SystemExit(f"{label} workflow {workflow_id} step id must be a non-empty string")
    if not isinstance(step.get("label"), str) or not step["label"]:
        raise SystemExit(f"{label} workflow {workflow_id} step {step_id} label is required")
    return step_id


def _action_id(label: str, workflow_id: str, action: object) -> str:
    if not isinstance(action, dict):
        raise SystemExit(f"{label} workflow {workflow_id} actions must contain objects")
    action_id = action.get("id")
    if not isinstance(action_id, str) or not action_id:
        raise SystemExit(f"{label} workflow {workflow_id} action id must be a non-empty string")
    if action.get("kind") not in {"confirm_start_bot_proposal", "review", "custom"}:
        raise SystemExit(f"{label} workflow {workflow_id} action {action_id} kind is invalid")
    if not isinstance(action.get("label"), str) or not action["label"]:
        raise SystemExit(f"{label} workflow {workflow_id} action {action_id} label is required")
    if not isinstance(action.get("enabled"), bool):
        raise SystemExit(f"{label} workflow {workflow_id} action {action_id} enabled must be boolean")
    return action_id


def _section_id(label: str, workflow_id: str, section: object) -> str:
    if not isinstance(section, dict):
        raise SystemExit(f"{label} workflow {workflow_id} sections must contain objects")
    section_id = section.get("id")
    if not isinstance(section_id, str) or not section_id:
        raise SystemExit(f"{label} workflow {workflow_id} section id must be a non-empty string")
    return section_id


def _validate_section(
    label: str,
    workflow_id: str,
    workflow: dict[str, Any],
    section: dict[str, Any],
    *,
    allowed_fields: set[str],
    allowed_section_kinds: set[str],
    action_ids: set[str],
) -> None:
    section_id = section["id"]
    component_kind = section.get("component_kind")
    if component_kind not in allowed_section_kinds:
        raise SystemExit(f"{label} workflow {workflow_id} section {section_id} component_kind is not allowed")
    fields = _section_fields(workflow_id, section, workflow)
    if fields and any(field not in allowed_fields for field in fields):
        raise SystemExit(f"{label} workflow {workflow_id} section {section_id} references an unknown field")
    action_id = section.get("action_id")
    if action_id is not None and action_id not in action_ids:
        raise SystemExit(f"{label} workflow {workflow_id} section {section_id} action_id is unknown")


def _workflow_allowed_fields(label: str, workflow_id: str, workflow: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("input_fields", "setup_fields", "allowed_fields"):
        values = workflow.get(key, [])
        if values is None:
            values = []
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise SystemExit(f"{label} workflow {workflow_id} {key} must be a string array")
        fields.extend(values)
    _ensure_unique(label, f"workflow {workflow_id} allowed fields", fields)
    return fields


def _workflow_required_input_fields(label: str, workflow_id: str, workflow: dict[str, Any]) -> list[str]:
    values = workflow.get("required_input_fields", [])
    if values is None:
        values = []
    if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
        raise SystemExit(f"{label} workflow {workflow_id} required_input_fields must be a string array")
    input_fields = set(workflow.get("input_fields", []))
    if any(field not in input_fields for field in values):
        raise SystemExit(f"{label} workflow {workflow_id} required_input_fields must be a subset of input_fields")
    _ensure_unique(label, f"workflow {workflow_id} required input fields", values)
    return list(values)


def _section_fields(
    workflow_id: str,
    section: dict[str, Any],
    workflow: dict[str, Any] | None,
) -> list[str]:
    if "fields_from" in section:
        fields_from = section["fields_from"]
        if fields_from not in {"input_fields", "setup_fields", "allowed_fields"}:
            raise SystemExit(f"workflow {workflow_id} section {section['id']} fields_from is invalid")
        if workflow is None:
            return []
        return list(workflow.get(fields_from, []))
    fields = section.get("fields", [])
    if fields is None:
        return []
    if not isinstance(fields, list) or any(not isinstance(item, str) or not item for item in fields):
        raise SystemExit(f"workflow {workflow_id} section {section['id']} fields must be a string array")
    return list(fields)


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _ensure_unique(label: str, name: str, values: list[str]) -> None:
    if len(set(values)) != len(values):
        raise SystemExit(f"{label} {name} must not contain duplicates")


def python_literal(value: object) -> str:
    return repr(value).replace("None", "None").replace("True", "True").replace("False", "False")


def normalized_python_definitions(contract: dict[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for workflow_id, workflow in contract["workflows"].items():
        allowed_fields = [
            *workflow.get("input_fields", []),
            *workflow.get("setup_fields", []),
            *workflow.get("allowed_fields", []),
        ]
        definitions[workflow_id] = {
            "workflow_id": workflow_id,
            "intent": workflow["intent"],
            "title": workflow["title"],
            "badges": workflow["badges"],
            "steps": [step["id"] for step in workflow["steps"]],
            "allowed_fields": allowed_fields,
            "required_input_fields": workflow.get("required_input_fields", []),
            "allowed_section_kinds": workflow["allowed_section_kinds"],
            "status_labels": workflow["status_labels"],
            "default_status": workflow["default_status"],
            "sections": normalized_sections(workflow_id, workflow),
            "actions": workflow.get("actions", []),
            "model_guidance": workflow["model_guidance"],
        }
    return definitions


def normalized_typescript_definitions(contract: dict[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for workflow_id, workflow in contract["workflows"].items():
        allowed_fields = [
            *workflow.get("input_fields", []),
            *workflow.get("setup_fields", []),
            *workflow.get("allowed_fields", []),
        ]
        definitions[workflow_id] = {
            "id": workflow_id,
            "intent": workflow["intent"],
            "title": workflow["title"],
            "aria_label": workflow["aria_label"],
            "icon_key": workflow["icon_key"],
            "badges": workflow["badges"],
            "steps": workflow["steps"],
            "status_labels": workflow["status_labels"],
            "default_status_key": workflow["default_status"],
            "allowed_section_kinds": workflow["allowed_section_kinds"],
            "allowed_fields": allowed_fields,
            "required_input_fields": workflow.get("required_input_fields", []),
            "sections": normalized_sections(workflow_id, workflow),
            "actions": workflow.get("actions", []),
            "model_guidance": workflow["model_guidance"],
        }
    return definitions


def normalized_sections(workflow_id: str, workflow: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in workflow.get("sections", []):
        normalized = dict(section)
        normalized.pop("fields_from", None)
        fields = _section_fields(workflow_id, section, workflow)
        if fields:
            normalized["fields"] = fields
        sections.append(normalized)
    return sections


def render_python(contract: dict[str, Any]) -> str:
    definitions = normalized_python_definitions(contract)
    return (
        "# Generated by scripts/sync-workflow-registry.py. Do not edit by hand.\n\n"
        f"WORKFLOW_SCHEMA_VERSION = {contract['schema_version']!r}\n"
        f"WORKFLOW_COMPONENT_KINDS = {python_literal(tuple(contract['component_kinds']))}\n"
        f"WORKFLOW_DEFINITIONS = {python_literal(definitions)}\n"
    )


def render_typescript(contract: dict[str, Any]) -> str:
    definitions = normalized_typescript_definitions(contract)
    component_kinds = json.dumps(contract["component_kinds"], indent=2)
    rendered_definitions = json.dumps(definitions, indent=2)
    return (
        "// Generated by scripts/sync-workflow-registry.py. Do not edit by hand.\n\n"
        f"export const WORKFLOW_SCHEMA_VERSION = {json.dumps(contract['schema_version'])} as const;\n"
        f"export const WORKFLOW_COMPONENT_KINDS = {component_kinds} as const;\n"
        f"export const WORKFLOW_DEFINITIONS = {rendered_definitions} as const;\n"
    )


def generated_files(contract: dict[str, Any]) -> dict[Path, str]:
    return {
        ROOT / "src" / "strategy_codebot" / "server" / "workflow_registry_contract.py": render_python(contract),
        ROOT / "apps" / "web" / "src" / "lib" / "workflow-registry-contract.ts": render_typescript(contract),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()

    contract = load_contract()
    files = generated_files(contract)
    if args.write:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return 0

    drifted = []
    for path, expected in files.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            actual = None
        if actual != expected:
            drifted.append(str(path.relative_to(ROOT)))
    if drifted:
        print("Workflow registry generated files are stale:")
        for path in drifted:
            print(f"- {path}")
        print("Run: scripts/sync-workflow-registry.py --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
