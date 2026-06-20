from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")


class RunWorker(Protocol):
    def run(self, task: Callable[[], T]) -> T: ...

    def readiness(self) -> dict[str, str | bool]: ...


@dataclass(frozen=True)
class InlineRunWorker:
    """Synchronous worker used until an external queue worker is enabled."""

    name: str = "inline"

    def run(self, task: Callable[[], T]) -> T:
        return task()

    def readiness(self) -> dict[str, str | bool]:
        return {
            "status": "ok",
            "mode": self.name,
            "background_queue": False,
        }
