"""프로그램 화면을 브라우저 탭이 아닌 '앱 창'(주소창·탭 없는 창)으로 연다.

윈도우에 기본으로 있는 Edge(없으면 Chrome)의 --app 모드를 쓴다.
"""
from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from pathlib import Path

from app.paths import config_dir

log = logging.getLogger(__name__)


def _candidates() -> list[Path]:
    out = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return out


def find_app_browser() -> str | None:
    for p in _candidates():
        if p.is_file():
            return str(p)
    return None


def open_app_window(url: str) -> bool:
    """앱 창으로 열었으면 True, 일반 브라우저로 열었으면 False."""
    exe = find_app_browser()
    if exe:
        profile = config_dir() / "appwindow"  # 따로 쓰는 프로필: 기존 브라우저 창과 섞이지 않게
        args = [exe, f"--app={url}", "--window-size=1280,900", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={profile}"]
        try:
            subprocess.Popen(args, close_fds=True)
            return True
        except OSError:
            log.exception("could not open app window")
    webbrowser.open(url)
    return False
