from pathlib import Path

from strategy_codebot.paths import repo_root, resolve_repo_path


def test_resolve_repo_path_prefers_existing_repo_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    resolved = resolve_repo_path(Path("configs/model-registry.example.yaml"))

    assert resolved == repo_root() / "configs/model-registry.example.yaml"


def test_resolve_repo_path_keeps_missing_relative_path_for_clear_errors(tmp_path: Path) -> None:
    missing = Path("missing/live-models.yaml")

    assert resolve_repo_path(missing) == missing
    assert resolve_repo_path(tmp_path / "missing.yaml") == tmp_path / "missing.yaml"
