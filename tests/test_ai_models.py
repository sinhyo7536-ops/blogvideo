"""2단계: 작업별 Claude 모델 설정, 연결 확인, 제미나이 제거 뒤의 키 정리."""
import base64
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from app import config, keybundle, secret
from app.ai import models as ai
from app.jobs import JobManager
from app.main import create_app
from tests.fakes import fake_steps


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))


@pytest.fixture
def client(tmp_path):
    manager = JobManager(tmp_path / "jobs", fake_steps())
    yield TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000)), manager
    manager.stop()


# ---------- 기본값과 저장 ----------
def test_defaults_balance_quality_and_cost():
    assert ai.current() == {"script": "claude-opus-5-5", "analysis": "claude-sonnet-5-5", "photos": "claude-sonnet-5-5",
                            "rewrite": "claude-sonnet-5-5", "review": "claude-haiku-5-5", "metadata": "claude-haiku-5-5"}
    assert all(t.default in ai.CHOICES for t in ai.TASKS.values())


def test_model_ids_live_only_in_the_settings_module():
    """모델 ID를 다른 코드에 흩어 두지 않는다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    for f in root.rglob("*.py"):
        if f.name == "models.py" and f.parent.name == "ai":
            continue
        text = f.read_text("utf-8")
        assert "claude-opus" not in text and "claude-sonnet" not in text and "claude-haiku" not in text, f


def test_save_keeps_only_changes_and_ignores_unknown():
    out = ai.save({"script": "claude-sonnet-5-5", "review": "claude-haiku-5-5", "photos": "gpt-9", "bogus": "x"})
    assert out["script"] == "claude-sonnet-5-5" and out["photos"] == "claude-sonnet-5-5"
    assert config.load_config()["models"] == {"script": "claude-sonnet-5-5"}       # 기본값과 같은 것은 저장 안 함
    assert ai.model_for("script") == "claude-sonnet-5-5" and ai.model_for("review") == "claude-haiku-5-5"
    ai.save({"script": "claude-opus-5-5"})                                           # 되돌리면 저장값도 비움
    assert config.load_config()["models"] == {}


def test_broken_saved_value_falls_back_to_default():
    config.save_config({"models": {"script": "deleted-model", "rewrite": 3}})
    assert ai.model_for("script") == "claude-opus-5-5" and ai.model_for("rewrite") == "claude-sonnet-5-5"
    config.save_config({"models": "nonsense"})
    assert ai.current()["analysis"] == "claude-sonnet-5-5"


# ---------- 실제 호출에 쓰이는 모델 ----------
class FakeClaude:
    def __init__(self, parsed):
        self.calls = []
        self.parsed = parsed
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.parsed)


def _post():
    from app.models import BlogPost, ImageRef

    return BlogPost(title="통영 여행", paragraphs=["동피랑에 갔어요."], url="u", images=[ImageRef(url="i")])


def test_script_uses_script_model_and_fallback():
    from app.script import ScriptDraft, SceneDraft, generate_script

    c = FakeClaude(ScriptDraft(scenes=[SceneDraft(text="a", image_index=0)], yt_title="t", yt_description="d",
                               yt_tags=[]))
    generate_script(_post(), "k", client=c)
    kw = c.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "medium"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]


def test_changed_setting_is_used_and_haiku_skips_fallback():
    from app.script import ScriptDraft, SceneDraft, generate_script

    ai.save({"script": "claude-haiku-5-5"})
    c = FakeClaude(ScriptDraft(scenes=[SceneDraft(text="a", image_index=0)], yt_title="t", yt_description="d",
                               yt_tags=[]))
    generate_script(_post(), "k", client=c)
    kw = c.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and "fallbacks" not in kw and "betas" not in kw


def test_director_uses_rewrite_model():
    from app.director import Direction, SceneDirection, direct
    from app.models import RenderOptions, Scene, VideoScript

    script = VideoScript(scenes=[Scene(text="a", image_index=0, direction="흑백")], yt_title="t", yt_description="d",
                         yt_tags=[])
    ai.save({"rewrite": "claude-opus-5-5"})
    c = FakeClaude(Direction(scenes=[SceneDirection(index=0, text="a", look="bw")]))
    out, _ = direct(script, RenderOptions(), "k", client=c)
    assert c.calls[0]["model"] == "claude-opus-5-5" and out.scenes[0].look == "bw"


# ---------- 연결 확인 ----------
def _status_error(cls, code, msg="x"):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(msg, response=httpx2.Response(code, request=req), body=None)


def test_check_reports_each_model_once():
    tried = []

    def create(**kw):
        tried.append(kw["model"])
        if kw["model"] == "claude-haiku-5-5":
            raise _status_error(anthropic.NotFoundError, 404)
        return SimpleNamespace(content=[])

    results = ai.check("k", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert tried == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    by = {r["model"]: r for r in results}
    assert by["claude-opus-5-5"]["ok"] and by["claude-opus-5-5"]["tasks"] == ["여행 대본 쓰기"]
    assert not by["claude-haiku-5-5"]["ok"] and "다른 모델" in by["claude-haiku-5-5"]["message"]
    assert set(by["claude-haiku-5-5"]["tasks"]) == {"맞춤법·영상 검수", "유튜브 제목·설명·태그"}


@pytest.mark.parametrize("err,word", [
    (lambda: _status_error(anthropic.AuthenticationError, 401), "API 키"),
    (lambda: _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), "크레딧"),
])
def test_check_explains_key_and_credit_problems(err, word):
    def create(**kw):
        raise err()

    results = ai.check("k", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert all(not r["ok"] and word in r["message"] for r in results)


def test_check_needs_a_key():
    from app.errors import UserError

    with pytest.raises(UserError, match="API 키"):
        ai.check("")


# ---------- 설정 API ----------
def test_settings_api_lists_and_saves_models(client):
    c, _ = client
    s = c.get("/api/settings").json()
    assert [t["key"] for t in s["models"]["tasks"]] == list(ai.TASKS)
    assert {x["id"] for x in s["models"]["choices"]} == set(ai.CHOICES)
    r = c.put("/api/settings", json={"models": {"photos": "claude-opus-5-5"}}).json()
    assert r["models"]["current"]["photos"] == "claude-opus-5-5"
    c.put("/api/settings", json={"channel_name": "채널"})                       # 다른 설정을 저장해도 모델은 그대로
    assert c.get("/api/settings").json()["models"]["current"]["photos"] == "claude-opus-5-5"


def test_check_models_api(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "check", lambda key: [{"model": "m", "label": "l", "tasks": [], "ok": True, "message": key}])
    assert c.post("/api/settings/check-models").json()[0]["message"] == "sk-ant-test1234"
    remote = TestClient(c.app, base_url="http://127.0.0.1:8765", client=("10.0.0.5", 5000))
    assert remote.post("/api/settings/check-models").status_code == 403


# ---------- 제미나이 제거 뒤 키 정리 ----------
def test_old_gemini_key_is_dropped_from_config():
    enc = base64.b64encode(secret.protect(b"AIza-old-key")).decode()
    (config.config_dir() / "config.json").write_text(json.dumps({"gemini_api_key_enc": enc, "channel_name": "c"}), "utf-8")
    data = config.load_config()
    assert "gemini_api_key" not in data and data["channel_name"] == "c"
    config.save_config(data)
    assert "gemini" not in (config.config_dir() / "config.json").read_text("utf-8")


def test_old_installer_with_gemini_key_imports_only_claude(tmp_path):
    f = config.config_dir() / keybundle.IMPORT_FILE
    f.write_text(keybundle.encode_keys({"anthropic_api_key": "sk-ant-abc12345", "gemini_api_key": "AIza-zzzz9999"}), "ascii")
    assert keybundle.import_pending_key() is True
    raw = (config.config_dir() / "config.json").read_text("utf-8")
    assert "gemini" not in raw and "AIza" not in raw and "sk-ant" not in raw     # 클로드 키는 암호화돼서 저장
    assert config.load_config()["anthropic_api_key"] == "sk-ant-abc12345"


def test_installer_with_only_gemini_key_imports_nothing():
    f = config.config_dir() / keybundle.IMPORT_FILE
    f.write_text(keybundle.encode_keys({"gemini_api_key": "AIza-zzzz9999"}), "ascii")
    assert keybundle.import_pending_key() is False and not f.exists()
    assert "gemini_api_key" not in config.load_config()


def test_keyed_installer_carries_only_claude_key(client):
    c, _ = client
    config.save_config({"anthropic_api_key": "sk-ant-abc12345"})
    from tests.test_keybundle import FAKE_SETUP

    r = c.post("/api/keyed-installer", files={"file": ("BlogVideo-Setup.exe", FAKE_SETUP, "application/octet-stream")})
    assert r.status_code == 200
    assert keybundle.read_trailer(r.content) == {"anthropic_api_key": "sk-ant-abc12345"}
