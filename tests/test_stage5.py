"""5단계: 대본 편집기 — 문단 AI 고치기, 맞춤법 검토, 버전 기록, 대본 확정."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import rewrite as rw
from app.jobs import JobManager, fingerprint
from app.main import create_app
from app.models import BlogPost, ImageRef, Scene, VideoScript
from tests.fakes import fake_steps, wait_stage

URL = "https://blog.naver.com/a/1"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))


class FakeClaude:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.answers.pop(0))


def _script(*texts):
    return VideoScript(scenes=[Scene(text=t, image_index=0) for t in texts], yt_title="t", yt_description="d",
                       yt_tags=[])


POST = BlogPost(title="통영 여행", paragraphs=["동피랑에 갔어요.", "회를 먹었어요."], images=[ImageRef(url="u")], url=URL)


# ---------- 문단 다시 쓰기 ----------
def test_command_text_joins_preset_and_free_text():
    assert "30%" in rw.command_text("shorter", None)
    assert rw.command_text(None, "  더   밝게 ") == "더 밝게"
    t = rw.command_text("polite", "사진 이야기도")
    assert "합쇼체" in t and t.endswith("사진 이야기도")
    assert rw.command_text("없는것", "") == ""


def test_rewrite_returns_only_requested_changed_scenes():
    answer = rw.RewriteDraft(scenes=[rw._WScene(index=1, text="  회가 정말   싱싱했어요. "),
                                     rw._WScene(index=0, text="다른 문단을 몰래 고침"),
                                     rw._WScene(index=2, text="그대로")])
    c = FakeClaude(answer)
    s = _script("동피랑에 왔어요.", "회를 먹었어요.", "그대로")
    out = rw.rewrite(s, [1, 2, 99], "감성 더하기", "k", POST, None, client=c)
    assert out == [{"index": 1, "before": "회를 먹었어요.", "after": "회가 정말 싱싱했어요."}]
    call = c.calls[0]
    assert call["model"] == "claude-sonnet-5-5"   # 작업 rewrite
    text = call["messages"][0]["content"]
    assert "[고칠 장면] 1, 2" in text and "[원문 블로그]" in text and "동피랑에 갔어요." in text


def test_rewrite_needs_targets_and_command():
    s = _script("a")
    assert rw.rewrite(s, [], "x", "k", client=FakeClaude()) == []
    assert rw.rewrite(s, [0], "  ", "k", client=FakeClaude()) == []


def test_review_keeps_only_fixes_found_in_the_text():
    answer = rw.ReviewDraft(fixes=[
        rw._WFix(index=0, before="갔었어요", after="갔어요", reason="어색한 시제"),
        rw._WFix(index=0, before="없는 글", after="x", reason="?"),
        rw._WFix(index=1, before="안돼요", after="안 돼요", reason="띄어쓰기"),
        rw._WFix(index=1, before="안돼요", after="안 돼요", reason="중복"),
        rw._WFix(index=5, before="a", after="b", reason="범위 밖"),
        rw._WFix(index=1, before="같음", after="같음", reason="변화 없음"),
    ])
    c = FakeClaude(answer)
    out = rw.review(_script("동피랑에 갔었어요.", "같음. 늦으면 안돼요."), "k", client=c)
    assert c.calls[0]["model"] == "claude-haiku-5-5"   # 작업 review
    assert [(f["index"], f["after"]) for f in out] == [(0, "갔어요"), (1, "안 돼요")]
    assert out[1]["text"] == "같음. 늦으면 안 돼요."


# ---------- 지문 ----------
def test_fingerprint_ignores_render_outputs_but_not_edits():
    a = _script("하나", "둘")
    b = a.model_copy(update={"scenes": [x.model_copy(update={"duration": 2.0, "audio_path": "x.mp3"})
                                        for x in a.scenes]})
    assert fingerprint(a) == fingerprint(b)
    c = a.model_copy(update={"scenes": [a.scenes[0].model_copy(update={"text": "하나!"}), a.scenes[1]]})
    assert fingerprint(a) != fingerprint(c)
    assert fingerprint(None) == ""


# ---------- API ----------
@pytest.fixture
def api(tmp_path):
    steps = fake_steps()
    seen = {"rewrite": [], "review": 0}

    def rewrite(script, indexes, command, key, post, analysis):
        seen["rewrite"].append((indexes, command, post.title if post else None))
        return [{"index": i, "before": script.scenes[i].text, "after": script.scenes[i].text + "!"} for i in indexes]

    def review(script, key):
        seen["review"] += 1
        return [{"index": 0, "before": "장면", "after": "장면이에요", "reason": "문장", "text": "장면이에요"}]

    steps.rewrite, steps.review = rewrite, review
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager, seen
    manager.stop()


def _ready(c, manager):
    job = c.post("/api/jobs", json={"url": URL, "mode": "api"}).json()
    wait_stage(manager, job["id"], "ready")
    return job["id"]


def _put(c, job_id, texts, label=""):
    script = _script(*texts).model_dump()
    return c.put(f"/api/jobs/{job_id}/script", json={"script": script, "options": {}, "version_label": label})


def test_render_needs_confirmation(api):
    c, manager, _ = api
    job_id = _ready(c, manager)
    r = c.post(f"/api/jobs/{job_id}/render")
    assert r.status_code == 400 and "대본 확정" in r.json()["message"]
    j = c.post(f"/api/jobs/{job_id}/confirm").json()
    assert j["is_confirmed"] is True
    assert c.post(f"/api/jobs/{job_id}/render").status_code == 200
    wait_stage(manager, job_id, "done")


def test_editing_after_confirm_needs_confirm_again(api):
    c, manager, _ = api
    job_id = _ready(c, manager)
    original = manager.get(job_id).script.model_dump()
    c.post(f"/api/jobs/{job_id}/confirm")
    j = _put(c, job_id, ["바꾼 장면"]).json()
    assert j["is_confirmed"] is False
    assert c.post(f"/api/jobs/{job_id}/render").status_code == 400
    j = c.put(f"/api/jobs/{job_id}/script", json={"script": original, "options": {}}).json()
    # 확정한 내용으로 되돌리면 다시 확정 상태
    assert j["is_confirmed"] is True


def test_confirm_refuses_empty_script(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": URL, "mode": "manual"}).json()
    wait_stage(manager, job["id"], "ready")
    assert c.post(f"/api/jobs/{job['id']}/confirm").status_code == 400


def test_ai_rewrite_and_review_return_suggestions_only(api):
    c, manager, seen = api
    job_id = _ready(c, manager)
    before = manager.get(job_id).script
    r = c.post(f"/api/jobs/{job_id}/ai-rewrite", json={"indexes": [0], "preset": "shorter", "command": "밝게"})
    assert r.status_code == 200 and r.json()["suggestions"][0]["after"] == "장면!"
    assert seen["rewrite"][0][0] == [0] and "30%" in seen["rewrite"][0][1] and "밝게" in seen["rewrite"][0][1]
    assert seen["rewrite"][0][2] == "제목: 테스트/글"
    r = c.post(f"/api/jobs/{job_id}/ai-review")
    assert r.json()["fixes"][0]["after"] == "장면이에요"
    assert manager.get(job_id).script == before        # 제안만, 대본은 그대로


def test_ai_rewrite_needs_command_and_key(api):
    c, manager, _ = api
    job_id = _ready(c, manager)
    r = c.post(f"/api/jobs/{job_id}/ai-rewrite", json={"indexes": [0]})
    assert r.status_code == 400 and "고칠지" in r.json()["message"]
    assert c.post(f"/api/jobs/{job_id}/ai-rewrite", json={"indexes": [], "preset": "natural"}).status_code == 400
    manager.steps.api_key = lambda: ""
    r = c.post(f"/api/jobs/{job_id}/ai-review")
    assert r.status_code == 400 and "키" in r.json()["message"]


def test_versions_record_ai_script_edits_and_restore(api):
    c, manager, _ = api
    job_id = _ready(c, manager)
    vs = c.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    assert [v["label"] for v in vs] == ["AI가 쓴 대본"]
    _put(c, job_id, ["자동 저장"])                        # 이름 없는 저장(자동 저장)은 버전을 남기지 않음
    _put(c, job_id, ["AI가 고친 글"], label="AI 고치기 전")
    assert c.post(f"/api/jobs/{job_id}/versions", json={"label": "내 버전"}).json()["n"] == 3
    vs = c.get(f"/api/jobs/{job_id}/versions").json()["versions"]
    assert [v["label"] for v in vs] == ["내 버전", "AI 고치기 전", "AI가 쓴 대본"]
    assert vs[1]["first"] == "자동 저장"
    j = c.post(f"/api/jobs/{job_id}/versions/1/restore").json()
    assert [s["text"] for s in j["script"]["scenes"]] == ["장면"]
    assert c.get(f"/api/jobs/{job_id}/versions").json()["versions"][0]["label"] == "되돌리기 전"
    assert c.post(f"/api/jobs/{job_id}/versions/99/restore").status_code == 400


def test_rewriting_whole_script_keeps_previous_version(api):
    c, manager, _ = api
    job_id = _ready(c, manager)
    c.post(f"/api/jobs/{job_id}/write", json={"mode": "api"})
    wait_stage(manager, job_id, "ready")
    labels = [v["label"] for v in c.get(f"/api/jobs/{job_id}/versions").json()["versions"]]
    assert labels == ["AI가 쓴 대본", "다시 쓰기 전", "AI가 쓴 대본"]


def test_versions_are_capped(api, monkeypatch):
    import app.jobs as jobs

    monkeypatch.setattr(jobs, "MAX_VERSIONS", 3)
    c, manager, _ = api
    job_id = _ready(c, manager)
    for i in range(5):
        c.post(f"/api/jobs/{job_id}/versions", json={"label": f"v{i}"})
    assert [v["label"] for v in manager.versions(job_id)] == ["v4", "v3", "v2"]


def test_direction_after_confirm_stays_confirmed(api):
    c, manager, _ = api

    def direct(script, options, key):
        return script.model_copy(update={"scenes": [script.scenes[0].model_copy(
            update={"motion": "zoom_in", "direction_done": script.scenes[0].direction})]}), options

    manager.steps.direct = direct
    job_id = _ready(c, manager)
    script = manager.get(job_id).script
    script = script.model_copy(update={"scenes": [script.scenes[0].model_copy(update={"direction": "확대"})]})
    c.put(f"/api/jobs/{job_id}/script", json={"script": script.model_dump(), "options": {}})
    c.post(f"/api/jobs/{job_id}/confirm")
    c.post(f"/api/jobs/{job_id}/render")
    s = wait_stage(manager, job_id, "done")
    assert s.script.scenes[0].motion == "zoom_in" and manager.is_confirmed(job_id)
