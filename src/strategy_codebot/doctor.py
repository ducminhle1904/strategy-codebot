from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from strategy_codebot import __version__
from strategy_codebot.harness import harness_cli_path
from strategy_codebot.knowledge import check_registry
from strategy_codebot.paths import repo_root
from strategy_codebot.reporting import validation_check
from strategy_codebot.schemas import schema
from strategy_codebot.tool_runtime import check_tool_registry


REQUIRED_FILES = (
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    ".github/workflows/ci.yml",
    ".github/workflows/release-artifacts.yml",
    "pyproject.toml",
    "configs/source-registry.yaml",
    "configs/tool-registry.yaml",
    "schemas/agent-run.schema.json",
    "schemas/knowledge-diff.schema.json",
    "schemas/knowledge-proposal.schema.json",
    "schemas/knowledge-snapshot.schema.json",
    "schemas/review-report.schema.json",
    "schemas/runtime-trace.schema.json",
    "schemas/strategy-spec.schema.json",
    "schemas/tool-contract.schema.json",
    "schemas/tool-event.schema.json",
    "schemas/validation-report.schema.json",
)


def doctor_report() -> dict[str, Any]:
    root = repo_root()
    checks: list[dict[str, str]] = []
    warnings: list[str] = []
    next_actions: list[str] = []

    checks.append(validation_check("python_version", sys.version_info >= (3, 11), f"Python {platform.python_version()}"))
    checks.append(validation_check("package_import", bool(__version__), f"strategy_codebot {__version__} importable."))

    for relative_path in REQUIRED_FILES:
        checks.append(validation_check(f"required_file:{relative_path}", (root / relative_path).exists(), f"Required file exists: {relative_path}"))

    _append_report_status(checks, warnings, next_actions, "source_registry", lambda: check_registry(root / "configs" / "source-registry.yaml", offline=True))
    _append_report_status(checks, warnings, next_actions, "tool_registry", lambda: check_tool_registry(root / "configs" / "tool-registry.yaml"))
    _append_schema_checks(checks)

    harness_path = harness_cli_path()
    harness_exists = harness_path.exists()
    harness_status = "present" if harness_exists else "missing_optional"
    if not harness_exists:
        warnings.append("Optional repository-harness CLI is not installed at scripts/bin/harness-cli.")
    checks.append({"name": "optional_harness_cli", "status": "pass", "details": f"{harness_status}: {harness_path}"})

    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    return {
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "next_actions": next_actions if status == "pass" else [*next_actions, "Fix failed doctor checks before release."],
        "environment": {
            "python_version": platform.python_version(),
            "package_version": __version__,
            "cwd": str(Path.cwd()),
            "repo_root": str(root),
            "harness_cli": {"status": harness_status, "path": str(harness_path)},
        },
    }


def _append_report_status(
    checks: list[dict[str, str]],
    warnings: list[str],
    next_actions: list[str],
    name: str,
    build_report: Callable[[], dict[str, Any]],
) -> None:
    try:
        report = build_report()
    except Exception as exc:
        checks.append({"name": name, "status": "fail", "details": f"{type(exc).__name__}: {exc}"})
        next_actions.append(f"{name}: fix report generation failure.")
        return
    status = str(report.get("status", "fail"))
    checks.append({"name": name, "status": "pass" if status == "pass" else "fail", "details": f"{name} status={status}"})
    warnings.extend(f"{name}: {warning}" for warning in report.get("warnings", []))
    next_actions.extend(f"{name}: {action}" for action in report.get("next_actions", []))


def _append_schema_checks(checks: list[dict[str, str]]) -> None:
    schema_names = [path.name for path in (repo_root() / "schemas").glob("*.schema.json")]
    for schema_name in sorted(schema_names):
        try:
            Draft202012Validator.check_schema(schema(schema_name))
        except Exception as exc:
            checks.append({"name": f"schema_load:{schema_name}", "status": "fail", "details": str(exc)})
        else:
            checks.append({"name": f"schema_load:{schema_name}", "status": "pass", "details": "Schema is loadable."})
