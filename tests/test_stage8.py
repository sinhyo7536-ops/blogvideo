"""8단계: 쇼츠, 영상 검수, 유튜브 글(제목 후보·설명·챕터·해시태그·썸네일 문구·키워드)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import checkup as ck
from app.ai import shorts as sh
from app.ai import youtube as yt
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from tests.fakes import fake_steps, render_job, wait_stage

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


POST = BlogPost(title="통영 여행", paragraphs=["p"], url=URL, images=[ImageRef(url=f"u{i}") for i in range(6)])


def _script(n=6, dur=5.0):
    places = ["동피랑", "동피랑", "중앙시장", "중앙시장", "케이블카", ""]
    return VideoScript(scenes=[Scene(text=f"장면 {i} 이야기", image_index=i, place=places[i % 6],
                                     section="ending" if i == n - 1 else "place", duration=dur)
                               for i in range(n)],
                       yt_title="통영 1박 2일", yt_description="d", yt_tags=["통영"], intro_title="통영 여행")


# ---------- 챕터 ----------
def test_chapters_follow_places_and_youtube_rules():
    marks = yt.chapters(_script(), RenderOptions())
    assert marks == [(0.0, "동피랑"), (13.0, "중앙시장"), (23.0, "케이블카")]
    assert marks[0][0] == 0 and len(marks) >= 3
    assert all(b[0] - a[0] >= 10 for a, b in zip(marks, marks[1:]))
    assert yt.chapter_text(marks).splitlines()[1] == "0:13 중앙시장"


def test_short_chapters_are_merged_and_too_few_means_none():
    assert yt.chapters(_script(dur=1.0), RenderOptions()) == []          # 모두 10초 미만 → 챕터 없음
    no_dur = _script().model_copy(update={"scenes": [Scene(text="a", image_index=0)]})
    assert yt.chapters(no_dur, RenderOptions()) == []


def test_youtube_text_cleans_and_adds_chapters_and_link():
    d = yt.YoutubeDraft(titles=["통영 여행 ", "통영 여행", "  감성   통영  "], description="소개\n\n방문한 곳\n· 동피랑",
                        hashtags=["#통영 여행", "통영여행", "거제"], thumbnail_texts=["통영 1박2일"], keywords=["통영 코스"])
    c = FakeClaude(d)
    out = yt.generate(_script(), POST, None, RenderOptions(), "k", client=c)
    assert c.calls[0]["model"] == "claude-haiku-5-5"                         # 작업 metadata
    assert out["titles"] == ["통영 여행", "감성 통영"] and out["hashtags"] == ["통영여행", "거제"]
    assert out["hashtag_text"] == "#통영여행 #거제"
    assert "챕터\n0:00 동피랑\n0:13 중앙시장" in out["description"] and out["description"].endswith(f"원문: {URL}")
    assert out["chapters"][1] == {"at": 13.0, "label": "중앙시장"}


# ---------- 쇼츠 ----------
def test_shorts_build_keeps_photos_and_limits_length():
    script = _script()
    d = sh.ShortsDraft(hook="통영에서 이걸 안 하면 손해", hook_photo=4,
                       scenes=[sh._WShortScene(index=3, text="시장 회!"), sh._WShortScene(index=1, text="벽화 골목"),
                               sh._WShortScene(index=99, text="없는 장면"), sh._WShortScene(index=1, text="중복")],
                       title="통영 꿀팁")
    s = sh.build(d, script, 6)
    assert [x.text for x in s.scenes] == ["통영에서 이걸 안 하면 손해", "벽화 골목", "시장 회!"]   # 시간 순서
    assert [x.image_index for x in s.scenes] == [4, 1, 3] and s.scenes[1].place == "동피랑"
    assert s.yt_title == "통영 꿀팁 #shorts" and "shorts" in s.yt_tags
    long = sh.ShortsDraft(hook="훅", hook_photo=-1, title="t",
                          scenes=[sh._WShortScene(index=i, text="가" * 90) for i in range(6)])
    s2 = sh.build(long, script, 6)
    assert sum(len(x.text) for x in s2.scenes) <= sh.MAX_CHARS and s2.scenes[0].image_index == 0


def test_shorts_plan_uses_script_model():
    d = sh.ShortsDraft(hook="훅", hook_photo=0, scenes=[sh._WShortScene(index=0, text="a")], title="t")
    c = FakeClaude(d)
    sh.plan(_script(), POST, {"photos": [{"index": 0, "category": "풍경", "description": "바다", "highlight": True}]},
            "k", client=c)
    assert c.calls[0]["model"] == "claude-opus-5-5"
    assert "사진 0: [풍경] 바다 (눈길을 끄는 사진)" in c.calls[0]["messages"][0]["content"]


# ---------- 검수 ----------
def test_local_checks_find_problems():
    s = _script()
    scenes = list(s.scenes)
    scenes[2] = scenes[2].model_copy(update={"image_index": 0})                     # 장면 0의 사진을 다시 씀
    scenes[3] = scenes[3].model_copy(update={"image_index": None})                  # 사진 없음
    scenes[4] = scenes[4].model_copy(update={"text": "가" * 70, "duration": 4.0})   # 길고 빠른 자막
    scenes[5] = scenes[5].model_copy(update={"photo_score": 20, "duration": 14.0})
    s = s.model_copy(update={"scenes": scenes, "excluded_images": [1], "yt_title": ""})
    items = ck.local_checks(s, POST, RenderOptions())
    text = " | ".join(f"{x['scene']}:{x['text']}" for x in items)
    for want in ("1:뺀 사진(사진 2)", "2:사진 1을 장면 1에서도", "3:사진이 없어", "4:자막이 70자", "4:자막이 빨리",
                 "5:사진 한 장이 14초", "5:글과 사진이 잘 안 맞을", "-1:유튜브 제목이 비어"):
        assert want in text, want


def test_checkup_adds_ai_typos_only_with_key():
    s = _script()
    review = lambda script, key: [{"index": 1, "before": "이야기", "after": "얘기", "reason": "구어체"}]
    r = ck.checkup(s, POST, RenderOptions(), "k", review)
    assert r["counts"]["typo"] == 1 and r["items"][0]["scene"] <= r["items"][-1]["scene"]
    assert ck.checkup(s, POST, RenderOptions(), "", review)["counts"]["typo"] == 0


# ---------- API ----------
@pytest.fixture
def api(tmp_path):
    steps = fake_steps()
    steps.youtube_text = lambda script, post, analysis, options, key: {"titles": ["제목 A"], "description": "설명",
                                                                       "hashtags": ["통영"], "hashtag_text": "#통영",
                                                                       "thumbnail_texts": ["통영 꿀팁"],
                                                                       "keywords": ["통영"], "chapters": []}
    steps.plan_shorts = lambda script, post, analysis, key: VideoScript(
        scenes=[Scene(text="훅이에요", image_index=0), Scene(text="본문", image_index=0)], yt_title="t #shorts",
        yt_description="d", yt_tags=["shorts"])
    steps.review = lambda script, key: []
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager
    manager.stop()


def _done(c, manager):
    job = c.post("/api/jobs", json={"url": URL, "mode": "api"}).json()
    wait_stage(manager, job["id"], "ready")
    render_job(c, job["id"])
    wait_stage(manager, job["id"], "done")
    return job["id"]


def test_youtube_text_is_saved(api):
    c, manager = api
    job_id = _done(c, manager)
    r = c.post(f"/api/jobs/{job_id}/youtube").json()
    assert r["titles"] == ["제목 A"] and c.get(f"/api/jobs/{job_id}").json()["youtube"]["hashtag_text"] == "#통영"


def test_shorts_plan_edit_and_render_keeps_main_video(api):
    c, manager = api
    job_id = _done(c, manager)
    plan = c.post(f"/api/jobs/{job_id}/shorts/plan").json()["script"]
    assert [s["text"] for s in plan["scenes"]] == ["훅이에요", "본문"]
    plan["scenes"][1]["text"] = "고친 본문"
    plan["scenes"].append({"text": "  ", "image_index": 0})                    # 빈 장면은 뺌
    assert c.post(f"/api/jobs/{job_id}/shorts", json={"script": plan}).status_code == 200
    s = wait_stage(manager, job_id, "done")
    j = c.get(f"/api/jobs/{job_id}").json()
    assert j["has_shorts"] and j["has_video"] and "shorts_path" not in j
    assert [x.text for x in s.shorts_script.scenes] == ["훅이에요", "고친 본문"]
    r = c.get(f"/api/jobs/{job_id}/shorts/video?download=1")
    assert r.status_code == 200 and "shorts.mp4" in r.headers["content-disposition"]


def test_shorts_failure_returns_to_result_with_message(api):
    from app.errors import UserError

    c, manager = api
    job_id = _done(c, manager)

    def broken(*a, **k):
        raise UserError("음성을 만들지 못했어요.")

    manager.steps.synthesize_all = broken
    c.post(f"/api/jobs/{job_id}/shorts", json={"script": manager.get(job_id).script.model_dump()})
    s = wait_stage(manager, job_id, "done")
    assert s.shorts_error == "음성을 만들지 못했어요." and s.video_path


def test_empty_shorts_refused(api):
    c, manager = api
    job_id = _done(c, manager)
    r = c.post(f"/api/jobs/{job_id}/shorts", json={"script": {"scenes": [{"text": " ", "image_index": 0}],
                                                              "yt_title": "t", "yt_description": "", "yt_tags": []}})
    assert r.status_code == 400


def test_thumbnail_text_can_be_changed(api, tmp_path):
    from PIL import Image

    c, manager = api
    job_id = _done(c, manager)
    before = manager.get(job_id).finished_at
    j = c.post(f"/api/jobs/{job_id}/thumbnail", json={"text": "통영 꿀팁"}).json()
    assert j["has_thumbnail"] and j["finished_at"] >= before
    with Image.open(manager.dir(job_id) / "render" / "thumbnail.jpg") as im:
        assert im.size == (1280, 720)


def test_checkup_endpoint(api):
    c, manager = api
    job_id = _done(c, manager)
    r = c.post(f"/api/jobs/{job_id}/checkup").json()
    assert set(r) == {"items", "fixes", "counts"}
