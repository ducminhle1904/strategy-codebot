from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").exists():
        return source_root
    return Path(__file__).resolve().parents[1]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_repo_path(path: Path) -> Path:
    if path.exists() or path.is_absolute():
        return path
    packaged_path = repo_root() / path
    return packaged_path if packaged_path.exists() else path
