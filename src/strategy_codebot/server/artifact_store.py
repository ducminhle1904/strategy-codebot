import json
from pathlib import Path
from typing import Any

from strategy_codebot.paths import repo_root
from strategy_codebot.server.repository import ArtifactRecord


class LocalArtifactStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else repo_root() / ".strategy-codebot" / "api-artifacts"

    def run_path(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def run_dir(self, run_id: str) -> Path:
        path = self.run_path(run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def storage_key(self, run_id: str, relative_path: str) -> str:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path must be relative to the run directory")
        return str(Path("runs") / run_id / relative)

    def read_content(self, artifact: ArtifactRecord) -> Any:
        path = self._path_for_key(artifact.storage_key)
        if artifact.mime_type == "application/json":
            return json.loads(path.read_text(encoding="utf-8"))
        return path.read_text(encoding="utf-8")

    def read_text_preview(self, artifact: ArtifactRecord, max_bytes: int) -> tuple[str, bool]:
        path = self._path_for_key(artifact.storage_key)
        with path.open("rb") as handle:
            content = handle.read(max_bytes + 1)
        truncated = len(content) > max_bytes
        if truncated:
            content = content[:max_bytes]
        return content.decode("utf-8", errors="ignore"), truncated

    def _path_for_key(self, storage_key: str) -> Path:
        root = self.root.resolve()
        path = (self.root / storage_key).resolve()
        if path != root and root not in path.parents:
            raise ValueError("artifact storage key escapes artifact root")
        return path
