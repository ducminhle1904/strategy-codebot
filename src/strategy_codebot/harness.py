from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from strategy_codebot.paths import repo_root


def harness_cli_path() -> Path:
    return repo_root() / "scripts" / "bin" / "harness-cli"


def should_record_harness(requested: bool | None) -> bool:
    if requested is not None:
        return requested
    return harness_cli_path().exists()


def build_trace_command(
    summary: str,
    story: str | None,
    agent: str,
    outcome: str,
    changed: Sequence[str],
    notes: str | None = None,
) -> list[str]:
    command = [
        str(harness_cli_path()),
        "trace",
        "--summary",
        summary,
        "--agent",
        agent,
        "--outcome",
        outcome,
        "--changed",
        ",".join(changed),
    ]
    if story:
        command.extend(["--story", story])
    if notes:
        command.extend(["--notes", notes])
    return command


def harness_outcome(validation_status: str) -> str:
    return {
        "pass": "completed",
        "fail": "failed",
        "manual_required": "partial",
        "skipped": "partial",
    }.get(validation_status, "partial")


def record_trace(command: list[str]) -> None:
    subprocess.run(command, cwd=repo_root(), check=True)
