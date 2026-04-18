from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = _ROOT / "config.json"
CACHE_DIR = _ROOT / ".cache"

DEFAULT_CONFIG: dict[str, Any] = {
    "odds_api_key": "",
    "default_sport": "americanfootball_nfl",
    "default_region": "us",
    "default_markets": "h2h,spreads,totals",
    "preferred_bookmakers": [
        "draftkings", "fanduel", "betmgm", "caesars", "pointsbetus", "betrivers"
    ],
    "bankroll": 1000.0,
    "kelly_fraction": 0.25,
    "use_demo_data_when_no_key": True,
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data or {})
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def read_cache_json(name: str, max_age_seconds: int = 300) -> Any | None:
    p = cache_path(name)
    if not p.exists():
        return None
    try:
        age = os.path.getmtime(p)
        import time
        if time.time() - age > max_age_seconds:
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_cache_json(name: str, data: Any) -> None:
    with open(cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f)
