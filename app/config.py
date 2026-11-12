from __future__ import annotations

import base64
import json
import logging

from app import secret
from app.paths import config_dir

log = logging.getLogger(__name__)
SECRET_KEYS = ("anthropic_api_key", "gemini_api_key")


def _file():
    return config_dir() / "config.json"


def load_config() -> dict:
    f = _file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    for name in SECRET_KEYS:
        enc = data.pop(name + "_enc", None)
        if enc:
            try:
                data[name] = secret.unprotect(base64.b64decode(enc)).decode("utf-8")
            except Exception:  # 다른 PC/계정에서 복사된 파일 등
                log.warning("stored %s could not be decrypted", name)
                data[name] = ""
    return data


def save_config(data: dict) -> None:
    out = dict(data)
    for name in SECRET_KEYS:
        key = out.pop(name, None)
        if key:
            out[name + "_enc"] = base64.b64encode(secret.protect(key.encode("utf-8"))).decode("ascii")
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)
