"""최종 검토에서 나온 문제의 재현 테스트."""
import time
from pathlib import Path

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.captions import supported_text
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions
from app.procutil import quiet_kwargs
from app.render import resolve_bgm
from app.script import generate_script
from tests.fakes import fake_steps, wait_stage
from tests.test_script import FakeClient, _req


# C1 + M9 ------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["\\\\192.168.0.66\\s\\a.mp3", "C:\\Users\\me\\rec.mp3", "/etc/passwd",
                                 "../x.mp3", "a/b.mp3", "song.wav"])
def test_bgm_rejects_paths(bad):
    with pytest.raises(ValidationError):
        RenderOptions(bgm=bad)


def test_bgm_volume_bounds():
    with pytest.raises(ValidationError):
        RenderOptions(bgm_volume=-1)
    with pytest.raises(ValidationError):
        RenderOptions(bgm_volume=5)


def test_resolve_bgm_only_from_music_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"x")
    assert resolve_bgm(str(outside)) is None
    assert resolve_bgm("잔잔한_피아노풍.mp3") is not None


def test_api_rejects_bgm_path_with_message(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(m), client=("192.168.0.5", 1))
    r = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {"bgm": "\\\\h\\s\\a.mp3"}})
    assert r.status_code == 400 and r.json()["message"] == "입력한 내용이 올바르지 않아요. 다시 확인해 주세요."
    m.stop()


# I1 -----------------------------------------------------------------------
def test_quiet_kwargs_on_windows():
    kw = quiet_kwargs("nt")
    assert kw["creationflags"] == 0x08000000 and "stdin" in kw


def test_quiet_kwargs_elsewhere():
    assert "creationflags" not in quiet_kwargs("posix")


# I2 + M6 ------------------------------------------------------------------
def test_finished_at_changes_on_rerender(tmp_path):
    m = JobManager(tmp_path, fake_steps())
    j = m.create("u", RenderOptions())
    wait_stage(m, j.id, "ready")
    m.start_render(j.id)
    first = wait_stage(m, j.id, "done").finished_at
    time.sleep(0.01)
    m.start_render(j.id)
    time.sleep(0.2)
    second = wait_stage(m, j.id, "done").finished_at
    assert first and second and second > first
    m.stop()


def test_api_hides_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(m), client=("192.168.0.5", 1))
    j = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}}).json()
    wait_stage(m, j["id"], "ready")
    c.post(f"/api/jobs/{j['id']}/render")
    wait_stage(m, j["id"], "done")
    body = c.get(f"/api/jobs/{j['id']}").text
    assert str(tmp_path) not in body and "local_path" not in body and "audio_path" not in body
    m.stop()


# I5 -----------------------------------------------------------------------
POST = BlogPost(title="t", paragraphs=["p"], images=[ImageRef(url="u")], url="u")


def _status_err(cls, code, msg):
    return cls(msg, response=httpx.Response(code, request=_req()), body={"error": {"message": msg}})


def test_credit_error_message_no_retry():
    c = FakeClient([_status_err(anthropic.BadRequestError, 400, "Your credit balance is too low to access the API")])
    with pytest.raises(Exception) as e:
        generate_script(POST, "k", client=c)
    assert "크레딧" in e.value.message and c.calls == 1


def test_rate_limit_message():
    c = FakeClient([_status_err(anthropic.RateLimitError, 429, "rate")])
    with pytest.raises(Exception) as e:
        generate_script(POST, "k", client=c)
    assert "잠시 뒤" in e.value.message and c.calls == 1


def test_overloaded_message():
    c = FakeClient([_status_err(anthropic.InternalServerError, 529, "overloaded")] * 2)
    with pytest.raises(Exception) as e:
        generate_script(POST, "k", client=c)
    assert "잠시 뒤" in e.value.message


def test_timeout_message():
    c = FakeClient([anthropic.APITimeoutError(request=_req())])
    with pytest.raises(Exception) as e:
        generate_script(POST, "k", client=c)
    assert "응답이 늦어요" in e.value.message


# I6 -----------------------------------------------------------------------
def test_worker_survives_save_failure(tmp_path):
    steps = fake_steps(fail_fetch_times=1)
    m = JobManager(tmp_path, steps)
    real_save = m._save
    state = {"n": 0}

    def flaky_save(s):
        if s.stage == "error" and state["n"] == 0:
            state["n"] += 1
            raise PermissionError("locked")
        real_save(s)

    m._save = flaky_save
    m.create("u1", RenderOptions())
    b = m.create("u2", RenderOptions())
    wait_stage(m, b.id, "ready")
    m.stop()


# M2 (re-graded) -----------------------------------------------------------
def test_supported_text_drops_missing_glyphs():
    assert supported_text("⭐ 별점 ♥") == "별점 ♥"
    assert supported_text("✅완료😀") == "완료"
