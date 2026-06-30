#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "workflow-registry.json"
INPUT_REQUEST_KINDS = {"text", "textarea", "single_select", "multi_select", "select_or_text", "boolean"}


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
    task_kinds = contract.get("task_kinds")
    if not _string_list(task_kinds) or len(set(task_kinds)) != len(task_kinds):
        raise SystemExit(f"{label} task_kinds must be a unique non-empty string array")
    workflows = contract.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise SystemExit(f"{label} workflows must be a non-empty object")
    for workflow_id, workflow in workflows.items():
        _validate_workflow(label, workflow_id, workflow, set(component_kinds), set(task_kinds))


def _validate_workflow(
    label: str,
    workflow_id: object,
    workflow: object,
    component_kinds: set[str],
    task_kinds: set[str],
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
    option_set_ids = _validate_option_sets(label, workflow_id, workflow)
    input_request_ids = _validate_input_request_templates(
        label,
        workflow_id,
        workflow,
        allowed_fields=set(allowed_fields),
        option_set_ids=option_set_ids,
    )
    _validate_task_templates(
        label,
        workflow_id,
        workflow,
        step_ids=set(step_ids),
        task_kinds=task_kinds,
        input_request_ids=input_request_ids,
        action_ids=set(action_ids),
    )
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
    if "optional" in step and not isinstance(step["optional"], bool):
        raise SystemExit(f"{label} workflow {workflow_id} step {step_id} optional must be boolean")
    if "skip_label" in step and (not isinstance(step["skip_label"], str) or not step["skip_label"]):
        raise SystemExit(f"{label} workflow {workflow_id} step {step_id} skip_label must be a non-empty string")
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


def _validate_option_sets(label: str, workflow_id: str, workflow: dict[str, Any]) -> set[str]:
    option_sets = workflow.get("option_sets", {})
    if option_sets is None:
        option_sets = {}
    if not isinstance(option_sets, dict):
        raise SystemExit(f"{label} workflow {workflow_id} option_sets must be an object")
    option_set_ids: list[str] = []
    for option_set_id, options in option_sets.items():
        if not isinstance(option_set_id, str) or not option_set_id:
            raise SystemExit(f"{label} workflow {workflow_id} option_set ids must be non-empty strings")
        if not isinstance(options, list) or not options:
            raise SystemExit(f"{label} workflow {workflow_id} option_set {option_set_id} must be a non-empty array")
        option_ids = [_option_id(label, workflow_id, f"option_set {option_set_id}", option) for option in options]
        option_values = [_option_value(label, workflow_id, f"option_set {option_set_id}", option) for option in options]
        _ensure_unique(label, f"workflow {workflow_id} option_set {option_set_id} options", option_ids)
        _ensure_unique(label, f"workflow {workflow_id} option_set {option_set_id} option values", option_values)
        option_set_ids.append(option_set_id)
    _ensure_unique(label, f"workflow {workflow_id} option sets", option_set_ids)
    return set(option_set_ids)


def _option_id(label: str, workflow_id: str, owner: str, option: object) -> str:
    if not isinstance(option, dict):
        raise SystemExit(f"{label} workflow {workflow_id} {owner} options must contain objects")
    option_id = option.get("id")
    if not isinstance(option_id, str) or not option_id:
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option id is required")
    if not isinstance(option.get("value"), str) or not option["value"]:
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option {option_id} value is required")
    if not isinstance(option.get("label"), str) or not option["label"]:
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option {option_id} label is required")
    if "description" in option and not isinstance(option["description"], str):
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option {option_id} description must be a string")
    if "disabled" in option and not isinstance(option["disabled"], bool):
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option {option_id} disabled must be boolean")
    if "tone" in option and option["tone"] not in {"neutral", "success", "warning", "danger"}:
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option {option_id} tone is invalid")
    return option_id


def _option_value(label: str, workflow_id: str, owner: str, option: object) -> str:
    if not isinstance(option, dict) or not isinstance(option.get("value"), str) or not option["value"]:
        raise SystemExit(f"{label} workflow {workflow_id} {owner} option value is required")
    return option["value"]


def _validate_input_request_templates(
    label: str,
    workflow_id: str,
    workflow: dict[str, Any],
    *,
    allowed_fields: set[str],
    option_set_ids: set[str],
) -> set[str]:
    requests = workflow.get("input_request_templates", [])
    if requests is None:
        requests = []
    if not isinstance(requests, list):
        raise SystemExit(f"{label} workflow {workflow_id} input_request_templates must be an array")
    request_ids: list[str] = []
    for request in requests:
        if not isinstance(request, dict):
            raise SystemExit(f"{label} workflow {workflow_id} input_request_templates must contain objects")
        request_id = request.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise SystemExit(f"{label} workflow {workflow_id} input_request id is required")
        if request.get("kind") not in INPUT_REQUEST_KINDS:
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} kind is invalid")
        field = request.get("field")
        if not isinstance(field, str) or field not in allowed_fields:
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} field is unknown")
        if not isinstance(request.get("label"), str) or not request["label"]:
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} label is required")
        if not isinstance(request.get("required"), bool):
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} required must be boolean")
        for text_key in ("question", "placeholder", "helper_text", "custom_option_label"):
            if text_key in request and (not isinstance(request[text_key], str) or not request[text_key]):
                raise SystemExit(
                    f"{label} workflow {workflow_id} input_request {request_id} {text_key} must be a non-empty string"
                )
        if "allow_custom" in request and not isinstance(request["allow_custom"], bool):
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} allow_custom must be boolean")
        option_set_id = request.get("option_set_id")
        if option_set_id is not None and option_set_id not in option_set_ids:
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} option_set_id is unknown")
        if request["kind"] in {"single_select", "multi_select", "select_or_text"} and option_set_id is None:
            raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} option_set_id is required")
        inline_options = request.get("options")
        inline_option_ids: list[str] = []
        if inline_options is not None:
            if not isinstance(inline_options, list) or not inline_options:
                raise SystemExit(f"{label} workflow {workflow_id} input_request {request_id} options must be a non-empty array")
            owner = f"input_request {request_id}"
            inline_option_ids = [_option_id(label, workflow_id, owner, option) for option in inline_options]
            inline_option_values = [_option_value(label, workflow_id, owner, option) for option in inline_options]
            _ensure_unique(label, f"workflow {workflow_id} input_request {request_id} options", inline_option_ids)
            _ensure_unique(label, f"workflow {workflow_id} input_request {request_id} option values", inline_option_values)
        recommended_option_id = request.get("recommended_option_id")
        if recommended_option_id is not None:
            if not isinstance(recommended_option_id, str) or not recommended_option_id:
                raise SystemExit(
                    f"{label} workflow {workflow_id} input_request {request_id} recommended_option_id must be a non-empty string"
                )
            option_ids = set(inline_option_ids)
            if option_set_id is not None:
                options = workflow.get("option_sets", {}).get(option_set_id, [])
                option_ids.update(option.get("id") for option in options if isinstance(option, dict))
            if recommended_option_id not in option_ids:
                raise SystemExit(
                    f"{label} workflow {workflow_id} input_request {request_id} recommended_option_id is unknown"
                )
        request_ids.append(request_id)
    _ensure_unique(label, f"workflow {workflow_id} input requests", request_ids)
    return set(request_ids)


def _validate_task_templates(
    label: str,
    workflow_id: str,
    workflow: dict[str, Any],
    *,
    step_ids: set[str],
    task_kinds: set[str],
    input_request_ids: set[str],
    action_ids: set[str],
) -> None:
    tasks = workflow.get("task_templates", [])
    if tasks is None:
        tasks = []
    if not isinstance(tasks, list):
        raise SystemExit(f"{label} workflow {workflow_id} task_templates must be an array")
    task_ids: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise SystemExit(f"{label} workflow {workflow_id} task_templates must contain objects")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise SystemExit(f"{label} workflow {workflow_id} task id is required")
        if task.get("kind") not in task_kinds:
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} kind is invalid")
        if task.get("step_id") not in step_ids:
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} step_id is unknown")
        if not isinstance(task.get("title"), str) or not task["title"]:
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} title is required")
        if not isinstance(task.get("blocking"), bool):
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} blocking must be boolean")
        if not isinstance(task.get("default_status"), str) or not task["default_status"]:
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} default_status is required")
        if "resume_on_complete" in task and not isinstance(task.get("resume_on_complete"), bool):
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} resume_on_complete must be boolean")
        if "resume_intent" in task and (
            not isinstance(task.get("resume_intent"), str) or not task["resume_intent"]
        ):
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} resume_intent must be a non-empty string")
        task_input_ids = task.get("input_request_ids", [])
        if not isinstance(task_input_ids, list) or any(item not in input_request_ids for item in task_input_ids):
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} input_request_ids contain unknown ids")
        task_action_ids = task.get("action_ids", [])
        if not isinstance(task_action_ids, list) or any(item not in action_ids for item in task_action_ids):
            raise SystemExit(f"{label} workflow {workflow_id} task {task_id} action_ids contain unknown ids")
        _ensure_unique(label, f"workflow {workflow_id} task {task_id} input_request_ids", task_input_ids)
        _ensure_unique(label, f"workflow {workflow_id} task {task_id} action_ids", task_action_ids)
        task_ids.append(task_id)
    _ensure_unique(label, f"workflow {workflow_id} tasks", task_ids)


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
            "optional_steps": [step["id"] for step in workflow["steps"] if step.get("optional") is True],
            "allowed_fields": allowed_fields,
            "required_input_fields": workflow.get("required_input_fields", []),
            "allowed_section_kinds": workflow["allowed_section_kinds"],
            "status_labels": workflow["status_labels"],
            "default_status": workflow["default_status"],
            "sections": normalized_sections(workflow_id, workflow),
            "actions": workflow.get("actions", []),
            "option_sets": workflow.get("option_sets", {}),
            "input_request_templates": workflow.get("input_request_templates", []),
            "task_templates": workflow.get("task_templates", []),
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
            "option_sets": workflow.get("option_sets", {}),
            "input_request_templates": workflow.get("input_request_templates", []),
            "task_templates": workflow.get("task_templates", []),
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
        f"WORKFLOW_TASK_KINDS = {python_literal(tuple(contract['task_kinds']))}\n"
        f"WORKFLOW_DEFINITIONS = {python_literal(definitions)}\n"
    )


def render_typescript(contract: dict[str, Any]) -> str:
    definitions = normalized_typescript_definitions(contract)
    component_kinds = json.dumps(contract["component_kinds"], indent=2)
    task_kinds = json.dumps(contract["task_kinds"], indent=2)
    rendered_definitions = json.dumps(definitions, indent=2)
    return (
        "// Generated by scripts/sync-workflow-registry.py. Do not edit by hand.\n\n"
        f"export const WORKFLOW_SCHEMA_VERSION = {json.dumps(contract['schema_version'])} as const;\n"
        f"export const WORKFLOW_COMPONENT_KINDS = {component_kinds} as const;\n"
        f"export const WORKFLOW_TASK_KINDS = {task_kinds} as const;\n"
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
