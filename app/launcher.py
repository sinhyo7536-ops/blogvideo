"""데스크톱 실행 진입점: 서버를 띄우고 브라우저를 연다."""
from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
import threading
import webbrowser
from typing import Callable

import httpx

from app.paths import config_dir

DEFAULT_PORT = 8765
HOST = "127.0.0.1"  # 이 PC에서만 접속 가능
START_FAILED = "프로그램을 시작하지 못했어요. 컴퓨터를 다시 켠 뒤 실행해 주세요."


def _setup_logging() -> None:
    logs = config_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / "app.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    # 콘솔 없는 실행(PyInstaller windowed)에서는 stdout/stderr가 None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _already_running(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/info", timeout=1.5)
        return r.status_code == 200 and "has_api_key" in r.json()
    except (httpx.HTTPError, ValueError):
        return False


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def server_config(app, port: int):
    import uvicorn

    return uvicorn.Config(app, host=HOST, port=port, log_config=None, access_log=False)


def _serve(app, port: int, on_ready: Callable[[], None]) -> None:
    import uvicorn

    server = uvicorn.Server(server_config(app, port))

    def watch():
        while not server.started and not server.should_exit:
            threading.Event().wait(0.1)
        if server.started:
            on_ready()

    threading.Thread(target=watch, daemon=True).start()
    server.run()


def _alert(message: str) -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "블로그 영상 변환기", 0x10)
    else:
        print(message, file=sys.stderr)


def main(port: int = DEFAULT_PORT, opener: Callable[[str], object] = webbrowser.open,
         serve: Callable = _serve, alert: Callable[[str], None] = _alert) -> int:
    _setup_logging()
    log = logging.getLogger("launcher")
    url = f"http://127.0.0.1:{port}"
    if _already_running(port):
        opener(url)
        return 0
    if not _port_free(port):
        log.error("port %s is busy", port)
        alert(START_FAILED)
        return 1

    from app.jobs import JobManager
    from app.main import create_app, default_steps
    from app.paths import jobs_dir

    manager = JobManager(jobs_dir(), default_steps())
    removed = manager.cleanup_old(7)
    log.info("starting on port %s (cleaned %s old jobs)", port, removed)
    serve(create_app(manager), port, lambda: opener(url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
