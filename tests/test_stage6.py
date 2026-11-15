"""6단계: 사진 매칭(관련도 점수·추천), 사진 빼기, 장면당 여러 사진, 사진별 시간."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.ai import photos as ph
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import MIN_PHOTO, photo_shares, render_video
from tests.conftest import make_silence
from tests.fakes import fake_steps, render_job, wait_stage
from tests.test_travel import _duration, _frame

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


# ---------- 장면 값 ----------
def test_scene_photo_fields_are_cleaned():
    s = Scene(text="a", image_index=2, more_images=[3, 3, -1, "x", 2, 4, 5, 6, 7, 8, 9, 10],
              photo_secs=[1, "2", -3, 99, None], photo_score=150, photo_alts=[1, -2, 3])
    assert s.more_images == [3, 2, 4, 5, 6, 7, 8, 9, 10]
    assert s.photo_secs == [1.0, 2.0, 0.0, 30.0, 0.0]
    assert s.photo_score == 100 and s.photo_alts == [1, 3]
    assert s.photo_list() == [2, 3, 4, 5, 6, 7, 8, 9, 10]   # 첫 사진과 같은 번호는 한 번만
    assert len(Scene(text="a", image_index=0, more_images=list(range(1, 40))).more_images) == 14
    assert Scene(text="a", image_index=None, more_images=[1]).photo_list() == [1]
    old = Scene.model_validate({"text": "a", "image_index": 0})   # 예전 대본
    assert old.more_images == [] and old.photo_score == -1


# ---------- 사진별 시간 ----------
def test_photo_shares_auto_split_equally():
    shares, total = photo_shares(6.0, 3)
    assert total == 6.0 and shares == pytest.approx([2.0, 2.0, 2.0])


def test_photo_shares_fixed_and_auto():
    shares, total = photo_shares(6.0, 3, [3.0, 0, 0])
    assert total == 6.0 and shares == pytest.approx([3.0, 1.5, 1.5])


def test_fixed_times_longer_than_voice_extend_the_scene():
    shares, total = photo_shares(4.0, 2, [3.0, 4.0])
    assert total == 7.0 and shares == [3.0, 4.0]
    shares, total = photo_shares(2.0, 3, [3.0, 0, 0])            # 자동 사진도 최소 시간은 보인다
    assert total == pytest.approx(3.0 + 2 * MIN_PHOTO) and shares[1] == pytest.approx(MIN_PHOTO)


def test_fixed_times_shorter_than_voice_are_stretched():
    shares, total = photo_shares(6.0, 2, [1.0, 2.0])
    assert total == 6.0 and shares == pytest.approx([2.0, 4.0])


# ---------- 여러 사진 렌더링 ----------
@pytest.fixture
def photos3(tmp_path):
    imgs = []
    for i, color in enumerate(["red", "blue", "green"]):
        p = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (800, 600), color).save(p)
        imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
    return BlogPost(title="여행", paragraphs=["p"], images=imgs, url="u")


def _opts():
    return RenderOptions(quality="720p", intro=False, outro=False, title_bar=False, effects="soft")


def test_scene_with_three_photos_shows_each_in_turn(tmp_path, photos3):
    audio = str(make_silence(tmp_path / "v.mp3", 3.0))
    script = VideoScript(scenes=[Scene(text="세 장", image_index=0, more_images=[1, 2], duration=3.0, audio_path=audio)],
                         yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(photos3, script, _opts(), tmp_path / "w")
    assert abs(_duration(out) - 3.0) < 0.2
    colors = [_frame(out, t, 1280, 720)[150, 640] for t in (0.4, 1.5, 2.6)]
    assert colors[0][0] > 150 and colors[1][2] > 150 and colors[2][1] > 100   # 빨강 → 파랑 → 초록


def test_photo_times_set_by_user(tmp_path, photos3):
    audio = str(make_silence(tmp_path / "v.mp3", 2.0))
    script = VideoScript(scenes=[Scene(text="두 장", image_index=0, more_images=[1], photo_secs=[3.0, 1.0],
                                       duration=2.0, audio_path=audio)], yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(photos3, script, _opts(), tmp_path / "w")
    assert abs(_duration(out) - 4.0) < 0.2                  # 사진 시간의 합이 목소리보다 길어 장면이 늘어남
    assert _frame(out, 2.4, 1280, 720)[150, 640][0] > 150   # 3초까지는 첫 사진(빨강)
    assert _frame(out, 3.7, 1280, 720)[150, 640][2] > 150   # 그 뒤 파랑


def test_missing_extra_photo_is_skipped(tmp_path, photos3):
    audio = str(make_silence(tmp_path / "v.mp3", 1.5))
    script = VideoScript(scenes=[Scene(text="a", image_index=0, more_images=[9], duration=1.5, audio_path=audio)],
                         yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(photos3, script, _opts(), tmp_path / "w")
    assert abs(_duration(out) - 1.5) < 0.2


# ---------- AI 사진 매칭 ----------
POST = BlogPost(title="통영", paragraphs=["동피랑", "회"], url=URL,
                images=[ImageRef(url=f"u{i}", para=i % 2) for i in range(5)])
ANALYSIS = {"photos": [{"index": 0, "category": "풍경", "description": "벽화 골목", "place": "동피랑"},
                       {"index": 1, "category": "음식", "description": "회 한 접시", "place": "중앙시장",
                        "highlight": True}]}


def _script(n=3, excluded=()):
    return VideoScript(scenes=[Scene(text=f"장면 {i}", image_index=i) for i in range(n)], yt_title="t",
                       yt_description="d", yt_tags=[], excluded_images=list(excluded))


def test_photo_table_uses_analysis_descriptions():
    lines = ph.photo_table(POST, ANALYSIS)
    assert lines[1] == "사진 1: [음식] 회 한 접시 / 장소 중앙시장 / 문단 1 뒤 / 눈길을 끄는 사진"
    assert lines[4].startswith("사진 4: [기타] (설명 없음)")
    req = ph._request(_script(excluded=[3]), POST, ANALYSIS)
    assert "[쓰지 않을 사진] 3" in req and '"지금 사진": 2' in req


def test_match_filters_excluded_and_out_of_range():
    answer = ph.MatchDraft(scenes=[
        ph._WMatch(index=0, current_score=90, photos=[0], score=90, reason="벽화"),
        ph._WMatch(index=1, current_score=20, photos=[3, 9, 1], score=130, reason="회"),
        ph._WMatch(index=2, current_score=40, photos=[3], score=60, reason="?"),
        ph._WMatch(index=7, current_score=0, photos=[0], score=0, reason="없는 장면")])
    c = FakeClaude(answer)
    out = ph.match(_script(excluded=[3]), POST, ANALYSIS, "k", client=c)
    assert c.calls[0]["model"] == "claude-sonnet-5-5"            # 작업 photos
    assert [m["index"] for m in out] == [0, 1, 2]
    assert out[1]["best"] == 1 and out[1]["alts"] == [1] and out[1]["score"] == 100
    assert out[2]["best"] is None and out[2]["score"] == 0         # 뺀 사진만 추천하면 추천 없음
    assert out[1]["current_score"] == 20


def test_spread_avoids_reusing_far_photos():
    ms = [{"index": 0, "best": 1, "alts": [1, 2], "score": 80},
          {"index": 1, "best": 1, "alts": [1], "score": 80},          # 바로 다음 장면은 같은 사진 괜찮음
          {"index": 4, "best": 1, "alts": [1, 3], "score": 70}]
    out = ph.spread(ms)
    assert [m["best"] for m in out] == [1, 1, 3] and out[2]["score"] == 60


# ---------- API ----------
@pytest.fixture
def api(tmp_path):
    steps = fake_steps()
    seen = {"analyze": 0, "match": []}

    def analyze(post, key):
        seen["analyze"] += 1
        return {"theme": "t", "photos": [{"index": 0, "category": "풍경", "description": "바다"}]}

    def match(script, post, analysis, key):
        seen["match"].append(analysis)
        return [{"index": 0, "current": 0, "current_score": 30, "best": 0, "score": 30, "alts": [0], "reason": "r"}]

    steps.analyze, steps.match_photos = analyze, match
    steps.write_script = None   # 직접 작성 흐름 (분석 없이 대본)
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager, seen
    manager.stop()


def test_match_photos_analyzes_first_when_needed(api):
    c, manager, seen = api
    job = c.post("/api/jobs", json={"url": URL, "mode": "manual"}).json()
    wait_stage(manager, job["id"], "ready")
    s = manager.get(job["id"])
    manager.update_script(job["id"], s.script.model_copy(update={"scenes": [Scene(text="a", image_index=0)]}),
                          s.options)
    r = c.post(f"/api/jobs/{job['id']}/match-photos").json()
    assert r["matches"][0]["score"] == 30 and seen["analyze"] == 1
    assert seen["match"][0]["photos"][0]["description"] == "바다"
    c.post(f"/api/jobs/{job['id']}/match-photos")
    assert seen["analyze"] == 1                                    # 분석은 한 번만
    assert manager.get(job["id"]).script.scenes[0].photo_score == -1   # 제안만, 대본은 그대로


def test_excluded_and_multi_photo_fields_survive_save_and_render(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": URL, "mode": "manual"}).json()
    wait_stage(manager, job["id"], "ready")
    script = {"scenes": [{"text": "a", "image_index": 0, "more_images": [0, 1], "photo_secs": [2, 0],
                          "photo_score": 40, "photo_alts": [1]}],
              "yt_title": "t", "yt_description": "d", "yt_tags": [], "excluded_images": [2]}
    j = c.put(f"/api/jobs/{job['id']}/script", json={"script": script, "options": {}}).json()
    sc = j["script"]["scenes"][0]
    assert sc["more_images"] == [0, 1] and sc["photo_secs"] == [2.0, 0.0] and j["script"]["excluded_images"] == [2]
    assert render_job(c, job["id"]).status_code == 200
    wait_stage(manager, job["id"], "done")


def test_ai_photo_scores_do_not_undo_confirmation():
    from app.jobs import fingerprint

    a = VideoScript(scenes=[Scene(text="a", image_index=0)], yt_title="t", yt_description="d", yt_tags=[])
    b = a.model_copy(update={"scenes": [a.scenes[0].model_copy(update={"photo_score": 30, "photo_alts": [1]})]})
    c = a.model_copy(update={"scenes": [a.scenes[0].model_copy(update={"more_images": [1]})]})
    assert fingerprint(a) == fingerprint(b) and fingerprint(a) != fingerprint(c)
