"""[임시] Gemini 무료 API로 대본 만들기. Claude로 돌아갈 때 이 파일과 app/gemini.py를 지운다."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app import config, keybundle
from app.errors import UserError
from app.gemini import MODELS, generate_script_gemini
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions
from tests.fakes import fake_steps, wait_stage

POST = BlogPost(title="제목", paragraphs=["첫 문단", "둘째 문단"], url="https://blog.naver.com/a/1",
                images=[ImageRef(url="u1"), ImageRef(url="u2")])
GOOD = json.dumps({"scenes": [{"text": "안녕하세요", "image_index": 0}, {"text": "끝", "image_index": 7}],
                   "yt_title": "제목", "yt_description": "설명", "yt_tags": ["#태그"]}, ensure_ascii=False)


class FakeGemini:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(text=out)


def _err(cls, code, msg):
    return cls(code, {"error": {"code": code, "message": msg, "status": "X"}})


def test_success_uses_free_model_and_json_schema():
    c = FakeGemini([GOOD])
    s = generate_script_gemini(POST, "AIza-test", client=c)
    assert [x.text for x in s.scenes] == ["안녕하세요", "끝"]
    assert s.scenes[1].image_index in (0, 1) and s.yt_tags == ["태그"]
    call = c.calls[0]
    assert call["model"] == MODELS[0] == "gemini-3.8-flash"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema["properties"]["scenes"]
    assert "https://blog.naver.com/a/1" in str(call["contents"])
    assert call["config"].system_instruction


def test_falls_back_to_next_model_when_not_found():
    c = FakeGemini([_err(errors.ClientError, 404, "model not found"), GOOD])
    generate_script_gemini(POST, "k", client=c)
    assert [x["model"] for x in c.calls] == MODELS[:2]


def test_quota_message():
    c = FakeGemini([_err(errors.ClientError, 429, "RESOURCE_EXHAUSTED")])
    with pytest.raises(UserError) as e:
        generate_script_gemini(POST, "k", client=c)
    assert "하루" in e.value.message and "AI 없이" in e.value.message


@pytest.mark.parametrize("code,msg", [(400, "API key not valid. Please pass a valid API key."), (403, "leaked key")])
def test_bad_key_message(code, msg):
    c = FakeGemini([_err(errors.ClientError, code, msg)])
    with pytest.raises(UserError, match="Gemini API 키"):
        generate_script_gemini(POST, "k", client=c)


def test_bad_json_retried_once_then_fails():
    c = FakeGemini(["not json", "{}"])
    with pytest.raises(UserError, match="대본을 만들지 못했어요"):
        generate_script_gemini(POST, "k", client=c)
    assert len(c.calls) == 2


def test_overloaded_then_ok():
    c = FakeGemini([_err(errors.ServerError, 503, "UNAVAILABLE"), GOOD])
    assert generate_script_gemini(POST, "k", client=c).scenes


def test_no_key():
    with pytest.raises(UserError, match="Gemini API 키"):
        generate_script_gemini(POST, "")


# ---- 흐름·설정 ---------------------------------------------------------------
def test_job_gemini_mode(tmp_path):
    steps = fake_steps(key="")
    steps.gemini_key = lambda: "AIza-x"
    steps.gemini_script = lambda post, key: generate_script_gemini(post, key, client=FakeGemini([GOOD]))
    m = JobManager(tmp_path, steps)
    j = m.create("u", RenderOptions(), mode="gemini")
    assert wait_stage(m, j.id, "ready").script.scenes[0].text == "안녕하세요"
    m.stop()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    from app.main import default_steps
    m = JobManager(tmp_path / "jobs", default_steps())
    yield TestClient(create_app(m), client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765")
    m.stop()


def test_settings_store_gemini_key_encrypted(client, tmp_path):
    r = client.put("/api/settings", json={"gemini_api_key": "AIzaSyTEST98765"})
    assert r.status_code == 200 and r.json()["gemini_api_key"].endswith("8765")
    raw = (tmp_path / "config" / "config.json").read_text("utf-8")
    assert "gemini_api_key_enc" in raw
    assert config.load_config()["gemini_api_key"] == "AIzaSyTEST98765"
    assert client.get("/api/info").json()["has_gemini_key"] is True
    # Claude 키는 건드리지 않음
    assert "anthropic_api_key" not in config.load_config() or not config.load_config()["anthropic_api_key"]


def test_gemini_mode_without_key_400(client):
    r = client.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}, "mode": "gemini"})
    assert r.status_code == 400 and "Gemini API 키" in r.json()["message"]


# ---- 지인용 설치 파일에 두 키 모두 ---------------------------------------------
FAKE_SETUP = b"MZ" + b"\x00" * 100 + "BlogVideo".encode("utf-16-le") + b"\x90" * 5000


def test_keyed_installer_carries_both_keys(tmp_path, monkeypatch):
    out = keybundle.embed(FAKE_SETUP, {"anthropic_api_key": "sk-ant-a1", "gemini_api_key": "AIza-g1"})
    assert keybundle.read_trailer(out) == {"anthropic_api_key": "sk-ant-a1", "gemini_api_key": "AIza-g1"}
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    pending = tmp_path / "config" / keybundle.IMPORT_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(keybundle.encode_keys({"anthropic_api_key": "sk-ant-a1", "gemini_api_key": "AIza-g1"}), "ascii")
    assert keybundle.import_pending_key() is True
    cfg = config.load_config()
    assert cfg["anthropic_api_key"] == "sk-ant-a1" and cfg["gemini_api_key"] == "AIza-g1"


def test_old_single_key_bundle_still_imports(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    pending = tmp_path / "config" / keybundle.IMPORT_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(keybundle.encode_key("sk-ant-legacy"), "ascii")
    assert keybundle.import_pending_key() is True
    assert config.load_config()["anthropic_api_key"] == "sk-ant-legacy"


def test_api_keyed_installer_with_only_gemini_key(client):
    config.save_config({"gemini_api_key": "AIza-only"})
    r = client.post("/api/keyed-installer", files={"file": ("s.exe", FAKE_SETUP, "application/octet-stream")})
    assert r.status_code == 200 and keybundle.read_trailer(r.content) == {"gemini_api_key": "AIza-only"}
