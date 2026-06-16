from __future__ import annotations


def validation_check(name: str, condition: bool, details: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if condition else "fail", "details": details}


def aggregate_status(statuses: list[str] | set[str]) -> str:
    if not statuses:
        return "skipped"
    if "fail" in statuses:
        return "fail"
    if "manual_required" in statuses:
        return "manual_required"
    if "pass" in statuses:
        return "pass"
    return "skipped"
