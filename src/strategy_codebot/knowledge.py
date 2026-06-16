from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from strategy_codebot.paths import repo_root


REQUIRED_KEYS = {"id", "platform", "type", "trust_level", "freshness_ttl_days"}


def check_registry(registry_path: Path, offline: bool = True) -> dict[str, Any]:
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    if not sources:
        checks.append(_check("sources_present", False, "Registry must contain a non-empty sources list."))
    else:
        checks.append(_check("sources_present", True, f"Found {len(sources)} sources."))

    seen_ids: set[str] = set()
    for source in sources:
        source_id = str(source.get("id", "<missing>"))
        missing = sorted(key for key in REQUIRED_KEYS if key not in source)
        checks.append(_check(f"{source_id}:required_metadata", not missing, f"Missing keys: {', '.join(missing)}" if missing else "Required metadata present."))

        duplicate = source_id in seen_ids
        checks.append(_check(f"{source_id}:unique_id", not duplicate, "Duplicate source id." if duplicate else "Source id is unique."))
        seen_ids.add(source_id)

        has_url = "url" in source
        has_path = "path" in source
        checks.append(_check(f"{source_id}:locator", has_url ^ has_path, "Exactly one of url or path is required."))

        if has_url:
            parsed = urlparse(str(source["url"]))
            checks.append(_check(f"{source_id}:url", parsed.scheme in {"http", "https"} and bool(parsed.netloc), "External URL must be absolute HTTP(S)."))
            if offline:
                warnings.append(f"{source_id}: external URL shape checked only; network fetch skipped.")

        if has_path:
            local = repo_root() / str(source["path"])
            checks.append(_check(f"{source_id}:path", local.exists(), f"Internal path must exist: {source['path']}"))

    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    return {
        "platform": "both",
        "status": status,
        "checks": checks,
        "evidence": [str(registry_path)],
        "warnings": warnings,
        "next_actions": [] if status == "pass" else ["Fix source registry metadata before ingestion."],
    }


def _check(name: str, condition: bool, details: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if condition else "fail", "details": details}

