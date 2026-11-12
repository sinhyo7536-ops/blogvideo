"""앱 창으로 열기, 창을 닫으면 종료."""
import pytest
from fastapi.testclient import TestClient

from app import appwindow
from app.jobs import JobManager
from app.main import Presence, create_app
from tests.fakes import fake_steps


# ---- 브라우저 찾기 / 앱 모드로 열기 -------------------------------------------
def test_find_chrome_first(tmp_path, monkeypatch):
    edge = tmp_path / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe"
    for p in (edge, chrome):
        p.parent.mkdir(parents=True)
        p.write_text("")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert appwindow.find_app_browser() == str(chrome)


def test_find_edge_when_no_chrome(tmp_path, monkeypatch):
    edge = tmp_path / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    for k, v in {"ProgramFiles(x86)": "x86", "ProgramFiles": "pf", "LOCALAPPDATA": "local"}.items():
        monkeypatch.setenv(k, str(tmp_path / v))
    assert appwindow.find_app_browser() == str(edge)


def test_find_chrome_installed_for_one_user(tmp_path, monkeypatch):
    chrome = tmp_path / "local" / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    for k, v in {"ProgramFiles(x86)": "x86", "ProgramFiles": "pf", "LOCALAPPDATA": "local"}.items():
        monkeypatch.setenv(k, str(tmp_path / v))
    assert appwindow.find_app_browser() == str(chrome)


def test_open_uses_app_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(appwindow, "find_app_browser", lambda: "C:/Edge/msedge.exe")
    monkeypatch.setattr(appwindow.subprocess, "Popen", lambda args, **kw: calls.append(args))
    assert appwindow.open_app_window("http://127.0.0.1:8765") is True
    args = calls[0]
    assert args[0] == "C:/Edge/msedge.exe" and "--app=http://127.0.0.1:8765" in args
    assert any(a.startswith("--user-data-dir=") for a in args)


def test_open_falls_back_to_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(appwindow, "find_app_browser", lambda: None)
    monkeypatch.setattr(appwindow.webbrowser, "open", opened.append)
    assert appwindow.open_app_window("http://x") is False and opened == ["http://x"]


# ---- 창 닫힘 감지 -----------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_presence_exits_after_window_closed():
    c = Clock()
    p = Presence(clock=c)
    p.seen()
    c.t += 10
    assert not p.should_exit(busy=False)
    c.t += Presence.SILENCE + 1
    assert p.should_exit(busy=False)


def test_presence_bye_exits_quickly_but_not_on_reload():
    c = Clock()
    p = Presence(clock=c)
    p.seen()
    p.bye()
    c.t += 2
    p.seen()  # 새로고침: 바로 다시 신호
    c.t += Presence.AFTER_BYE + 1
    assert not p.should_exit(busy=False)
    p.bye()
    c.t += Presence.AFTER_BYE + 1
    assert p.should_exit(busy=False)


def test_presence_waits_for_running_job():
    c = Clock()
    p = Presence(clock=c)
    p.seen()
    c.t += 999
    assert not p.should_exit(busy=True)
    assert p.should_exit(busy=False)


def test_presence_gives_up_if_window_never_opened():
    c = Clock()
    p = Presence(clock=c)
    c.t += Presence.NEVER_OPENED - 1
    assert not p.should_exit(busy=False)
    c.t += 2
    assert p.should_exit(busy=False)


def test_heartbeat_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    app = create_app(m)
    c = TestClient(app, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765")
    assert app.state.presence.last_seen is None
    assert c.post("/api/heartbeat").status_code == 204
    assert app.state.presence.last_seen is not None
    assert c.post("/api/bye").status_code == 204
    assert app.state.presence.bye_at is not None
    m.stop()


def test_manager_busy_flag(tmp_path):
    from app.models import RenderOptions
    from tests.fakes import wait_stage

    m = JobManager(tmp_path, fake_steps(delay=0.3))
    assert m.is_busy() is False
    j = m.create("u", RenderOptions())
    assert m.is_busy() is True
    wait_stage(m, j.id, "ready")
    assert m.is_busy() is False
    m.stop()
