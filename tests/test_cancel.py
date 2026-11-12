"""만드는 중 멈추기."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import render
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from tests.conftest import make_silence
from tests.fakes import fake_steps, wait_stage

URL = "https://blog.naver.com/a/1"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    manager = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager
    manager.stop()


def _ready(c, manager):
    job = c.post("/api/jobs", json={"url": URL, "mode": "auto"}).json()
    wait_stage(manager, job["id"], "ready")
    return job["id"]


def test_stop_while_rendering_returns_to_editing(api):
    c, manager = api
    gate = threading.Event()
    aborted = []
    manager.steps.abort = lambda: (aborted.append(1), gate.set())

    def slow_render(post, script, options, workdir, on_progress=None):
        on_progress(0.1)
        gate.wait(5)                # FFmpeg가 도는 중 … 멈추면 풀림
        on_progress(0.5)            # 다음 진행 보고에서 멈춤이 확인됨
        raise AssertionError("멈춘 뒤에는 여기까지 오면 안 됨")

    manager.steps.render_video = slow_render
    job_id = _ready(c, manager)
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "rendering")
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    s = wait_stage(manager, job_id, "ready")
    assert aborted and s.script.scenes and s.failed_phase is None and s.message == "" and not s.video_path
    assert not manager.is_busy()
    manager.steps.render_video = fake_steps().render_video      # 다시 만들면 정상적으로 끝남
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")


def test_stop_while_making_voices(api):
    c, manager = api
    started = threading.Event()

    def slow_tts(script, voice, dest, on_progress=None, speed="normal"):
        for i in range(50):
            started.set()
            time.sleep(0.05)
            on_progress((i + 1) / 50)
        return script

    manager.steps.synthesize_all = slow_tts
    job_id = _ready(c, manager)
    c.post(f"/api/jobs/{job_id}/render")
    started.wait(5)
    c.post(f"/api/jobs/{job_id}/cancel")
    assert wait_stage(manager, job_id, "ready").script.scenes


def test_stop_while_ai_writes_keeps_loaded_post(api):
    c, manager = api
    gate = threading.Event()
    real = manager.steps.generate_script

    def slow_script(post, key):
        gate.wait(5)
        return real(post, key)

    manager.steps.generate_script = slow_script
    job = c.post("/api/jobs", json={"url": URL, "mode": "load"}).json()
    wait_stage(manager, job["id"], "loaded")
    c.post(f"/api/jobs/{job['id']}/write", json={"mode": "api"})
    wait_stage(manager, job["id"], "scripting")
    c.post(f"/api/jobs/{job['id']}/cancel")
    gate.set()
    s = wait_stage(manager, job["id"], "loaded")
    assert s.post is not None and s.script is None        # AI 결과는 버리고 불러온 글 화면으로


def test_stop_while_loading_blog(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    manager = JobManager(tmp_path / "jobs", fake_steps(delay=0.5))
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    job = c.post("/api/jobs", json={"url": URL, "mode": "load"}).json()
    wait_stage(manager, job["id"], "scraping")
    c.post(f"/api/jobs/{job['id']}/cancel")
    s = wait_stage(manager, job["id"], "error")
    assert "멈췄어요" in s.message and s.post is None
    manager.stop()


def test_cancel_when_nothing_is_running_changes_nothing(api):
    c, manager = api
    job_id = _ready(c, manager)
    assert c.post(f"/api/jobs/{job_id}/cancel").json()["stage"] == "ready"
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")
    assert c.post(f"/api/jobs/{job_id}/cancel").json()["stage"] == "done"
    assert c.post("/api/jobs/nope/cancel").status_code == 404


def test_stop_current_kills_running_ffmpeg(tmp_path):
    """실제 FFmpeg가 도는 중에 멈추면 몇 초 안에 끝난다."""
    img = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 1200), "teal").save(img)
    post = BlogPost(title="t", paragraphs=[], url="u", images=[ImageRef(url="i", local_path=str(img))])
    audio = str(make_silence(tmp_path / "a.mp3", 58.0))
    script = VideoScript(scenes=[Scene(text="긴 장면", image_index=0, duration=60.0, audio_path=audio)],
                         yt_title="t", yt_description="d", yt_tags=[])
    result = {}

    def work():
        try:
            render.render_video(post, script, RenderOptions(quality="1080p"), tmp_path / "w")
            result["ok"] = True
        except Exception as e:
            result["error"] = e

    assert render.stop_current() is False          # 도는 것이 없을 때
    t = threading.Thread(target=work)
    t.start()
    for _ in range(100):
        time.sleep(0.1)
        if render.stop_current():
            break
    started = time.time()
    t.join(15)
    assert not t.is_alive() and "error" in result and time.time() - started < 10
