import socket
import threading
import time

import pytest
import uvicorn

from app.jobs import JobManager
from app.main import create_app
from app import launcher
from tests.fakes import fake_steps


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    return tmp_path


def test_second_launch_only_opens_browser(home):
    port = free_port()
    m = JobManager(home / "jobs", fake_steps())
    server = uvicorn.Server(uvicorn.Config(create_app(m), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    opened, served = [], []
    code = launcher.main(port=port, opener=opened.append, serve=lambda *a: served.append(a), alert=pytest.fail)
    assert code == 0 and opened == [f"http://127.0.0.1:{port}"] and served == []
    server.should_exit = True
    t.join(5)
    m.stop()


def test_port_taken_by_other_program_alerts(home):
    port = free_port()
    blocker = socket.socket()
    blocker.bind((launcher.HOST, port))  # 같은 주소(127.0.0.1)를 다른 프로그램이 점유
    blocker.listen()
    alerts = []
    code = launcher.main(port=port, opener=pytest.fail, serve=lambda *a: pytest.fail("served"), alert=alerts.append)
    blocker.close()
    assert code == 1 and alerts == ["프로그램을 시작하지 못했어요. 컴퓨터를 다시 켠 뒤 실행해 주세요."]


def test_first_launch_serves_then_opens(home):
    port = free_port()
    opened = []

    def fake_serve(app, port_, on_ready):
        on_ready()

    code = launcher.main(port=port, opener=opened.append, serve=fake_serve, alert=pytest.fail)
    assert code == 0 and opened == [f"http://127.0.0.1:{port}"]
    assert (home / "config" / "logs" / "app.log").exists()
