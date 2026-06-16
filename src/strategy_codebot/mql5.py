from __future__ import annotations

from typing import Any


def runner_design(spec: dict[str, Any]) -> str:
    symbol = spec.get("symbol") or spec["market"]
    return "\n".join(
        [
            "# MQL5 Runner Design",
            "",
            "Phase 1 does not compile or run MQL5 code. This document defines the future runner contract.",
            "",
            "## Expected Inputs",
            "",
            f"- Symbol: `{symbol}`",
            f"- Timeframe: `{spec['timeframe']}`",
            "- Source file: future `.mq5` Expert Advisor or indicator.",
            "- Expert parameters: future `.set` file under `MQL5/Profiles/Tester`.",
            "- Tester config: future `.ini` file passed to `terminal64.exe /config:<path>`.",
            "",
            "## Expected Commands",
            "",
            "- Compile with MetaEditor/MetaEditor64 and capture compiler log.",
            "- Run MetaTrader 5 Strategy Tester with a generated config file.",
            "- Parse report output into `validation-report.schema.json`.",
            "",
            "## Phase 1 Status",
            "",
            "- Status: `manual_required`.",
            "- Missing environment: Windows runner with MetaEditor and MetaTrader 5.",
            "- No `.mq5` code is generated in Phase 1.",
            "",
        ]
    )


def validation_report() -> dict[str, Any]:
    return {
        "platform": "mql5",
        "status": "manual_required",
        "checks": [
            {
                "name": "mql5_runner_environment",
                "status": "manual_required",
                "details": "Requires a future Windows runner with MetaEditor and MetaTrader 5.",
            },
            {
                "name": "mql5_compile",
                "status": "skipped",
                "details": "Phase 1 does not compile MQL5 code.",
            },
        ],
        "evidence": ["mql5-runner-design.md"],
        "warnings": ["MQL5 compile and Strategy Tester execution are not implemented in Phase 1."],
        "next_actions": ["Implement the Windows MetaEditor/MetaTrader runner in a later phase."],
    }

