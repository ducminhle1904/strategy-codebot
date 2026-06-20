from typing import Any


def estimate_tokens(value: Any) -> int:
    return max(1, len(str(value)) // 4)
