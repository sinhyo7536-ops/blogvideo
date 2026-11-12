from __future__ import annotations

import json

from app.paths import config_dir


def _file():
    return config_dir() / "config.json"


def load_config() -> dict:
    f = _file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)
