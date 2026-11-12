import pytest
from fastapi.testclient import TestClient

from app.jobs import JobManager
from app.main import create_app
from tests.fakes import fake_steps, wait_stage


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    return tmp_path


def make_client(home, ip="127.0.0.1", key="sk-ant-test1234"):
    from app.config import save_config
    if key:
        save_config({"anthropic_api_key": key})
    steps = fake_steps(key=key)
    from app.config import load_config
    steps.api_key = lambda: load_config().get("anthropic_api_key", "")
    m = JobManager(home / "jobs", steps)
    return TestClient(create_app(m), client=(ip, 50000)), m


def test_index_served(home):
    c, m = make_client(home)
    assert "블로그" in c.get("/").text
    m.stop()


def test_info(home):
    c, m = make_client(home, ip="192.168.0.5")
    d = c.get("/api/info").json()
    assert d["lan_url"].startswith("http://") and d["lan_url"].endswith(":8765")
    assert d["qr_svg"].lstrip().startswith("<")
    assert d["has_api_key"] is True and d["is_local"] is False
    m.stop()


def test_settings_forbidden_from_lan(home):
    c, m = make_client(home, ip="192.168.0.5")
    r = c.put("/api/settings", json={"anthropic_api_key": "x"})
    assert r.status_code == 403 and r.json()["message"] == "설정은 PC에서만 바꿀 수 있어요."
    assert c.get("/api/settings").status_code == 403
    m.stop()


def test_settings_allowed_from_localhost(home):
    c, m = make_client(home, key="")
    assert c.put("/api/settings", json={"anthropic_api_key": " sk-ant-abcd1234 "}).status_code == 200
    masked = c.get("/api/settings").json()["anthropic_api_key"]
    assert masked.endswith("1234") and "abcd" not in masked
    m.stop()


def test_create_job_without_key_400(home):
    c, m = make_client(home, key="")
    r = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}})
    assert r.status_code == 400 and r.json()["message"] == "설정에서 API 키를 입력해 주세요."
    m.stop()


def test_create_job_bad_url_400(home):
    c, m = make_client(home)
    r = c.post("/api/jobs", json={"url": "https://tistory.com/1", "options": {}})
    assert r.status_code == 400 and "네이버 블로그" in r.json()["message"]
    m.stop()


def _done_job(c, m):
    j = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {"aspect": "9:16"}}).json()
    s = wait_stage(m, j["id"], "ready")
    r = c.put(f"/api/jobs/{j['id']}/script", json={"script": s.script.model_dump(), "options": {"aspect": "16:9"}})
    assert r.status_code == 200 and r.json()["options"]["aspect"] == "16:9"
    assert c.post(f"/api/jobs/{j['id']}/render").status_code == 200
    wait_stage(m, j["id"], "done")
    return j["id"]


def test_full_api_flow(home):
    c, m = make_client(home, ip="192.168.0.9")
    job_id = _done_job(c, m)
    d = c.get(f"/api/jobs/{job_id}").json()
    assert d["stage"] == "done" and "video_path" not in d
    assert c.get(f"/api/jobs/{job_id}/image/0").status_code == 200
    assert c.get(f"/api/jobs/{job_id}/image/5").status_code == 404
    m.stop()


def test_download_korean_filename(home):
    c, m = make_client(home)
    job_id = _done_job(c, m)
    r = c.get(f"/api/jobs/{job_id}/video?download=1")
    cd = r.headers["content-disposition"]
    assert r.status_code == 200 and cd.startswith("attachment") and "filename*=UTF-8''" in cd
    assert "%2F" not in cd and "/" not in cd.split("''", 1)[1]
    m.stop()


def test_video_supports_range(home):
    c, m = make_client(home)
    job_id = _done_job(c, m)
    r = c.get(f"/api/jobs/{job_id}/video", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == b"0123456789"
    m.stop()


def test_unknown_job_404(home):
    c, m = make_client(home)
    assert c.get("/api/jobs/nope").status_code == 404
    m.stop()


def test_music_list_and_upload(home):
    c, m = make_client(home)
    names = [x["name"] for x in c.get("/api/music").json()]
    assert "잔잔한_피아노풍.mp3" in names
    r = c.post("/api/music", files={"file": ("내 노래.mp3", b"ID3fake", "audio/mpeg")})
    assert r.status_code == 200
    assert {"name": "내 노래.mp3", "builtin": False} in c.get("/api/music").json()
    assert c.post("/api/music", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    m.stop()


def test_music_upload_forbidden_from_lan(home):
    c, m = make_client(home, ip="10.0.0.3")
    assert c.post("/api/music", files={"file": ("a.mp3", b"ID3", "audio/mpeg")}).status_code == 403
    m.stop()
