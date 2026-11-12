"""무료로 대본 만들기: AI 없이 자동 대본."""
import pytest
from fastapi.testclient import TestClient

from app.autoscript import CLOSING, auto_script
from app.errors import UserError
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions
from tests.fakes import fake_steps, wait_stage

LONG = ("오늘은 서울 성수동에 새로 생긴 카페를 다녀왔어요. 인테리어가 정말 예쁘고 넓어서 친구들과 오기 좋았습니다. "
        "시그니처 메뉴인 크림라떼는 달지 않고 고소했고, 디저트로 먹은 바스크 치즈케이크도 꾸덕해서 만족스러웠어요! "
        "주차는 건물 뒤편에 두 대 정도 가능하니 참고하세요.")
POST = BlogPost(title="성수동 신상 카페 솔직 후기", url="https://blog.naver.com/a/1",
                paragraphs=[LONG, "영업시간은 오전 11시부터 밤 10시까지예요.", "다음에 또 방문할 예정입니다."],
                images=[ImageRef(url=f"u{i}") for i in range(4)])


# ---- AI 없이 자동 대본 -------------------------------------------------------
def test_auto_script_shape():
    s = auto_script(POST)
    assert len(s.scenes) >= 3
    assert POST.title in s.scenes[0].text
    assert s.scenes[-1].text == CLOSING
    assert all(0 < len(x.text) <= 60 for x in s.scenes)
    assert all(x.image_index is not None and 0 <= x.image_index < 4 for x in s.scenes)
    assert s.yt_title == POST.title and POST.url in s.yt_description
    assert s.yt_tags and all(not t.startswith("#") for t in s.yt_tags)


def test_auto_script_without_images_or_text():
    s = auto_script(BlogPost(title="제목", paragraphs=[], images=[], url="u"))
    assert [x.image_index for x in s.scenes] == [None] * len(s.scenes)
    assert len(s.scenes) >= 2


def test_auto_script_keeps_the_whole_post():
    """글 전체가 들어간다: 문장을 골라내지 않고, 장면 수 제한도 없다."""
    many = BlogPost(title="t", url="u", images=[ImageRef(url=f"u{i}") for i in range(5)],
                    paragraphs=[f"{i}번째 문장입니다." for i in range(80)])
    s = auto_script(many)
    assert len(s.scenes) == 82                      # 제목 + 80문장 + 마무리
    assert [x.text for x in s.scenes[1:-1]] == [f"{i}번째 문장입니다." for i in range(80)]
    idx = [x.image_index for x in s.scenes]
    assert idx == sorted(idx) and set(idx) == {0, 1, 2, 3, 4}   # 사진은 순서대로 고르게


# ---- 작업 흐름 ---------------------------------------------------------------
def _mgr(tmp_path, key=""):
    steps = fake_steps(key=key)

    def no_api(post, api_key):
        raise AssertionError("API must not be called in free modes")

    steps.generate_script = no_api
    steps.api_key = lambda: key
    return JobManager(tmp_path, steps)


def test_job_auto_mode_needs_no_key(tmp_path):
    m = _mgr(tmp_path)
    j = m.create("u", RenderOptions(), mode="auto")
    s = wait_stage(m, j.id, "ready")
    assert s.script.scenes and s.mode == "auto"
    m.stop()


def test_switch_to_free_mode_after_api_error(tmp_path):
    steps = fake_steps()

    def broke(post, api_key):
        raise UserError("API 크레딧이 부족해요.")

    steps.generate_script = broke
    m = JobManager(tmp_path, steps)
    j = m.create("u", RenderOptions())
    wait_stage(m, j.id, "error")
    assert m.use_free_mode(j.id, "auto").stage == "ready"
    m.stop()


# ---- API ---------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = _mgr(tmp_path / "jobs")
    yield TestClient(create_app(m), client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765"), m
    m.stop()


def test_api_auto_mode_without_key(client):
    c, m = client
    r = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}, "mode": "auto"})
    assert r.status_code == 200
    wait_stage(m, r.json()["id"], "ready")


def test_api_paid_mode_without_key_suggests_free(client):
    c, _ = client
    r = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}, "mode": "api"})
    assert r.status_code == 400 and "무료" in r.json()["message"]



def test_manual_mode_is_gone(client):
    c, _ = client
    r = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}, "mode": "manual"})
    assert r.status_code == 400
    assert c.get("/api/jobs/x/prompt").status_code == 404
