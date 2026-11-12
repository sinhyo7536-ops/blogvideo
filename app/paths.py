from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def resource_path(rel: str) -> Path:
    """번들(PyInstaller) 또는 소스 트리 기준 리소스 경로."""
    base = Path(getattr(sys, "_MEIPASS", _ROOT))
    return base / rel


def _home() -> Path | None:
    h = os.environ.get("BLOGVIDEO_HOME")
    return Path(h) if h else None


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    if h := _home():
        return _ensure(h / "config")
    if os.name == "nt":
        return _ensure(Path(os.environ["APPDATA"]) / "BlogVideo")
    return _ensure(Path.home() / ".config" / "BlogVideo")


def jobs_dir() -> Path:
    if h := _home():
        return _ensure(h / "jobs")
    if os.name == "nt":
        return _ensure(Path(os.environ["LOCALAPPDATA"]) / "BlogVideo" / "jobs")
    return _ensure(Path.home() / ".local" / "share" / "BlogVideo" / "jobs")


def user_music_dir() -> Path:
    return _ensure(config_dir() / "music")


def music_dirs() -> list[Path]:
    return [resource_path("assets/music"), user_music_dir()]


def _bin(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    bundled = resource_path(f"ffmpeg/{exe}")
    if bundled.exists():
        return str(bundled)
    return shutil.which(name) or name


def ffmpeg_bin() -> str:
    return _bin("ffmpeg")


def ffprobe_bin() -> str:
    return _bin("ffprobe")
