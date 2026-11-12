from __future__ import annotations

import base64
import json
import logging

from app import secret
from app.paths import config_dir

log = logging.getLogger(__name__)
_KEY = "anthropic_api_key"
_ENC = "anthropic_api_key_enc"


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
    enc = data.pop(_ENC, None)
    if enc:
        try:
            data[_KEY] = secret.unprotect(base64.b64decode(enc)).decode("utf-8")
        except Exception:  # 다른 PC/계정에서 복사된 파일 등
            log.warning("stored API key could not be decrypted")
            data[_KEY] = ""
    return data


def save_config(data: dict) -> None:
    out = dict(data)
    key = out.pop(_KEY, None)
    if key:
        out[_ENC] = base64.b64encode(secret.protect(key.encode("utf-8"))).decode("ascii")
    f = _file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(f)
