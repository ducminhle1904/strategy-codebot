from pathlib import Path

from strategy_codebot.knowledge import check_registry


def test_source_registry_offline_check_passes() -> None:
    report = check_registry(Path("configs/source-registry.yaml"), offline=True)

    assert report["status"] == "pass"
    assert any(check["name"] == "sources_present" for check in report["checks"])
    assert report["warnings"]


def test_source_registry_reports_malformed_entries(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text("sources:\n  - not-a-mapping\n", encoding="utf-8")

    report = check_registry(registry, offline=True)

    assert report["status"] == "fail"
    assert report["checks"][1]["name"] == "source_0:mapping"
