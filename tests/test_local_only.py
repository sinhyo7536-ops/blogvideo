"""PC 전용 모드: 같은 와이파이의 다른 기기나 다른 웹사이트는 쓸 수 없어야 한다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config, secret
from app.jobs import JobManager
from app.launcher import server_config
from app.main import create_app
from tests.fakes import fake_steps


@pytest.fixture
def app_(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    yield create_app(m)
    m.stop()


def test_server_listens_only_on_loopback(app_):
    assert server_config(app_, 8765).host == "127.0.0.1"


@pytest.mark.parametrize("path", ["/", "/api/info", "/api/music", "/static/app.js"])
def test_other_devices_blocked(app_, path):
    c = TestClient(app_, client=("192.168.0.5", 1), base_url="http://127.0.0.1:8765")
    r = c.get(path)
    assert r.status_code == 403 and "설치된 PC에서만" in r.json()["message"]


def test_foreign_host_header_blocked(app_):
    # DNS 리바인딩: 다른 사이트 주소로 들어온 요청
    c = TestClient(app_, client=("127.0.0.1", 1), base_url="http://evil.example:8765")
    assert c.get("/api/info").status_code == 403


def test_localhost_name_allowed(app_):
    c = TestClient(app_, client=("127.0.0.1", 1), base_url="http://localhost:8765")
    assert c.get("/api/info").status_code == 200


def test_cross_site_post_blocked(app_):
    c = TestClient(app_, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765")
    r = c.post("/api/music", files={"file": ("a.mp3", b"ID3", "audio/mpeg")},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_post_allowed(app_):
    c = TestClient(app_, client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765")
    r = c.put("/api/settings", json={"anthropic_api_key": "sk-ant-zz1234"},
              headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


# ---- 키 암호화 -------------------------------------------------------------
def test_key_is_not_stored_in_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    monkeypatch.setattr(secret, "protect", lambda b: bytes(x ^ 0x5A for x in b))
    monkeypatch.setattr(secret, "unprotect", lambda b: bytes(x ^ 0x5A for x in b))
    config.save_config({"anthropic_api_key": "sk-ant-SECRET9999"})
    raw = (tmp_path / "config" / "config.json").read_text("utf-8")
    assert "SECRET9999" not in raw and "anthropic_api_key_enc" in raw
    assert config.load_config()["anthropic_api_key"] == "sk-ant-SECRET9999"


def test_legacy_plaintext_key_still_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.json").write_text(json.dumps({"anthropic_api_key": "sk-ant-old"}), "utf-8")
    assert config.load_config()["anthropic_api_key"] == "sk-ant-old"


def test_undecryptable_key_reads_as_missing(tmp_path, monkeypatch):
    # 설정 파일을 다른 PC로 복사해 간 경우: 복호화 실패 → 키 없음
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    monkeypatch.setattr(secret, "protect", lambda b: b[::-1])
    config.save_config({"anthropic_api_key": "sk-ant-abc"})

    def boom(b):
        raise OSError("wrong user")

    monkeypatch.setattr(secret, "unprotect", boom)
    assert config.load_config().get("anthropic_api_key", "") == ""


@pytest.mark.skipif(__import__("os").name != "nt", reason="윈도우 DPAPI 전용")
def test_real_dpapi_roundtrip():
    data = "sk-ant-한글키".encode()
    enc = secret.protect(data)
    assert enc != data and secret.unprotect(enc) == data
