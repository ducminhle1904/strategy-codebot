from pathlib import Path

from strategy_codebot.knowledge import check_registry


def test_source_registry_offline_check_passes() -> None:
    report = check_registry(Path("configs/source-registry.yaml"), offline=True)

    assert report["status"] == "pass"
    assert any(check["name"] == "sources_present" for check in report["checks"])
    assert report["warnings"]

