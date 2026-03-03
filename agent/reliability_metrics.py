"""
Simple persisted reliability counters for monitoring planner behavior.
"""

from __future__ import annotations

import json
from pathlib import Path


def _metrics_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "reliability_metrics.json"


def _load() -> dict:
    path = _metrics_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def increment_metric(key: str, count: int = 1) -> None:
    if not key:
        return
    data = _load()
    try:
        current = int(data.get(key, 0))
    except Exception:
        current = 0
    data[key] = current + max(1, int(count))
    with open(_metrics_path(), "w") as f:
        json.dump(data, f, indent=2)


def get_metrics() -> dict:
    return _load()
