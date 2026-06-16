from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def generate_live(prompt: str, model_registry: Path) -> tuple[dict[str, Any], str]:
    import litellm

    registry = yaml.safe_load(model_registry.read_text(encoding="utf-8"))
    model = registry["agents"]["pine_specialist"]["primary"]
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Return strict JSON with keys strategy_spec and pine_code. "
                    "strategy_spec must match the StrategySpec schema. "
                    "Do not claim runtime validation or profitability."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content
    payload = json.loads(content)
    return payload["strategy_spec"], payload["pine_code"]

