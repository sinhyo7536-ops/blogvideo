"""3단계: 프로젝트(목록·이름·삭제·이어 하기), 여러 글 합치기, 중복 글·사진, 실패한 글 다시 불러오기, 사진 올리기."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.errors import UserError
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef
from app.projects import append_post, merged_title, post_fingerprint
from tests.fakes import fake_steps, wait_stage

A, B, C = ("https://blog.naver.com/a/1", "https://blog.naver.com/b/2", "https://blog.naver.com/c/3")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))


def _jpg(seed: int) -> bytes:
    im = Image.new("RGB", (64, 48), (seed * 40 % 255, 90, 160))
    for x in range(0, 64, 8):                       # 사진마다 다른 무늬 (지문이 달라지게)
        for y in range(0, 48, 8):
            if (x * (seed + 1) + y * 3) % 5 < 2:
                im.paste((255, 255, 255), (x, y, x + 8, y + 8))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


# ---------- 합치기 ----------
def test_append_post_marks_boundaries_and_shifts_photo_positions():
    p1 = BlogPost(title="통영 1일", paragraphs=["a", "b"], url=A, date="2026. 10. 3.",
                  images=[ImageRef(url="x", para=-1), ImageRef(url="y", para=1)], missing=["2번째 사진"])
    p2 = BlogPost(title="통영 2일", paragraphs=["c"], url=B, images=[ImageRef(url="z", para=0)], duplicates=1)
    m = append_post(append_post(None, p1, 1, True), p2, 2, True)
    assert m.paragraphs == ["[1번째 글] 통영 1일", "a", "b", "[2번째 글] 통영 2일", "c"]
    assert [(i.para, i.post) for i in m.images] == [(0, 0), (2, 0), (4, 1)]
    assert m.missing == ["1번째 글의 2번째 사진"] and m.duplicates == 1 and m.date == "2026. 10. 3."
    single = append_post(None, p1, 1, False)
    assert single.paragraphs == ["a", "b"] and single.images[1].para == 1


def test_merged_title_and_fingerprint():
    assert merged_title(["통영 1일", "통영 2일", ""]) == "통영 1일 외 1편"
    assert merged_title(["하나"]) == "하나"
    p = BlogPost(title="제목", paragraphs=["본문  이에요"], images=[], url=A)
    q = BlogPost(title="제목", paragraphs=["본문 이에요"], images=[], url=B)    # 주소만 다름 (PC/모바일)
    assert post_fingerprint(p) == post_fingerprint(q)
    assert post_fingerprint(p) != post_fingerprint(q.model_copy(update={"title": "다른 글"}))


# ---------- 작업 ----------
@pytest.fixture
def api(tmp_path):
    steps = fake_steps()
    posts = {
        A: BlogPost(title="통영 1일", paragraphs=["동피랑"], images=[ImageRef(url="a0"), ImageRef(url="a1")], url=A),
        B: BlogPost(title="통영 2일", paragraphs=["케이블카"], images=[ImageRef(url="b0"), ImageRef(url="dup")], url=B),
        "https://m.blog.naver.com/a/1": None,
    }
    photos = {"a0": _jpg(1), "a1": _jpg(2), "b0": _jpg(3), "dup": _jpg(1)}   # dup = a0 와 같은 사진
    state = {"fail": {C}}

    def fetch(url):
        if url in state["fail"]:
            raise UserError("글을 읽을 수 없어요. 전체공개 글인지 확인해 주세요.")
        if url == "https://m.blog.naver.com/a/1":
            return posts[A].model_copy(update={"url": url})
        if url == C:
            return BlogPost(title="거제", paragraphs=["바다"], images=[ImageRef(url="c0")], url=C)
        return posts[url]

    def download(post, dest, known=None):
        from app.scraper.naver import image_hash, same_photo

        dest.mkdir(parents=True, exist_ok=True)
        seen = known if known is not None else []
        kept, dups = [], 0
        for im in post.images:
            data = photos.get(im.url) or _jpg(9)
            h = image_hash(data)
            if any(same_photo(h, k) for k in seen):
                dups += 1
                continue
            seen.append(h)
            p = dest / f"img_{len(list(dest.glob('img_*.jpg'))):02d}.jpg"
            p.write_bytes(data)
            kept.append(im.model_copy(update={"local_path": str(p)}))
        return post.model_copy(update={"images": kept, "duplicates": dups})

    steps.fetch_post, steps.download_images = fetch, download
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager, state
    manager.stop()


def test_several_posts_become_one_project(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": A, "urls": [B, A], "mode": "load"}).json()
    assert job["urls"] == [A, B]                                   # 같은 주소는 한 번만
    s = wait_stage(manager, job["id"], "loaded")
    assert s.post.title == "통영 1일 외 1편" and s.name == "통영 1일 외 1편"
    assert s.post.paragraphs == ["[1번째 글] 통영 1일", "동피랑", "[2번째 글] 통영 2일", "케이블카"]
    assert len(s.post.images) == 3 and s.post.duplicates == 1      # 다른 글의 같은 사진은 뺌
    assert [x["status"] for x in s.sources] == ["ok", "ok"] and s.sources[1]["duplicates"] == 1


def test_same_post_with_different_address_is_skipped(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": A, "urls": ["https://m.blog.naver.com/a/1"], "mode": "load"}).json()
    s = wait_stage(manager, job["id"], "loaded")
    assert [x["status"] for x in s.sources] == ["ok", "duplicate"] and "같은 글" in s.sources[1]["message"]
    assert s.post.title == "통영 1일"


def test_post_numbers_skip_duplicates_and_failures(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": A, "urls": ["https://m.blog.naver.com/a/1", C, B],
                                    "mode": "load"}).json()
    s = wait_stage(manager, job["id"], "loaded")
    assert [x["status"] for x in s.sources] == ["ok", "duplicate", "error", "ok"]
    assert [p for p in s.post.paragraphs if p.startswith("[")] == ["[1번째 글] 통영 1일", "[2번째 글] 통영 2일"]
    assert sorted({i.post for i in s.post.images}) == [0, 1]


def test_failed_post_is_listed_and_can_be_retried(api):
    c, manager, state = api
    job = c.post("/api/jobs", json={"url": A, "urls": [C], "mode": "load"}).json()
    s = wait_stage(manager, job["id"], "loaded")
    assert [x["status"] for x in s.sources] == ["ok", "error"] and "전체공개" in s.sources[1]["message"]
    state["fail"].clear()
    c.post(f"/api/jobs/{job['id']}/write", json={"mode": "manual"})
    wait_stage(manager, job["id"], "ready")
    assert c.post(f"/api/jobs/{job['id']}/sources/retry").status_code == 200
    s = wait_stage(manager, job["id"], "ready")
    assert [x["status"] for x in s.sources] == ["ok", "ok"]
    assert s.post.paragraphs[-2:] == ["[2번째 글] 거제", "바다"] and s.post.images[-1].post == 1
    assert s.script is not None and s.analysis is None
    r = c.post(f"/api/jobs/{job['id']}/sources/retry")
    assert r.status_code == 400 and "다시 불러올 글" in r.json()["message"]


def test_all_posts_failing_is_an_error(api):
    c, manager, state = api
    state["fail"].update({A, B})
    job = c.post("/api/jobs", json={"url": A, "urls": [B], "mode": "load"}).json()
    s = wait_stage(manager, job["id"], "error")
    assert "하나도" in s.message and len(s.sources) == 2


def test_upload_photo_adds_and_rejects_duplicates(api):
    c, manager, _ = api
    job = c.post("/api/jobs", json={"url": A, "mode": "manual"}).json()
    wait_stage(manager, job["id"], "ready")
    r = c.post(f"/api/jobs/{job['id']}/photos", files={"file": ("내 사진.png", _png(), "image/png")})
    assert r.status_code == 200
    imgs = r.json()["post"]["images"]
    assert len(imgs) == 3 and imgs[-1]["url"] == "upload:내 사진.png"
    assert c.get(f"/api/jobs/{job['id']}/image/2").status_code == 200
    r = c.post(f"/api/jobs/{job['id']}/photos", files={"file": ("again.jpg", _jpg(1), "image/jpeg")})
    assert r.status_code == 400 and "이미 들어 있는" in r.json()["message"]
    r = c.post(f"/api/jobs/{job['id']}/photos", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400 and "열 수 없어요" in r.json()["message"]


def _png() -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (80, 60), (10, 200, 30))
    im.paste((0, 0, 0), (10, 10, 40, 40))
    im.save(buf, "PNG")
    return buf.getvalue()


def test_project_list_rename_delete_and_survive_restart(api, tmp_path):
    c, manager, _ = api
    j1 = c.post("/api/jobs", json={"url": A, "mode": "manual"}).json()
    wait_stage(manager, j1["id"], "ready")
    j2 = c.post("/api/jobs", json={"url": B, "mode": "manual"}).json()
    wait_stage(manager, j2["id"], "ready")
    jobs = c.get("/api/jobs").json()["jobs"]
    assert [j["id"] for j in jobs] == [j2["id"], j1["id"]] and jobs[1]["name"] == "통영 1일"
    assert c.patch(f"/api/jobs/{j1['id']}", json={"name": "  가족 여행   영상 "}).json()["name"] == "가족 여행 영상"
    assert c.patch(f"/api/jobs/{j1['id']}", json={"name": ""}).status_code == 400
    assert c.delete(f"/api/jobs/{j2['id']}").status_code == 204
    assert not (tmp_path / "jobs" / j2["id"]).exists()
    assert c.get(f"/api/jobs/{j2['id']}").status_code == 404
    again = JobManager(tmp_path / "jobs", manager.steps)            # 껐다 켜도 남아 있음
    assert [s.name for s in again.list_jobs()] == ["가족 여행 영상"]
    again.stop()


def test_cannot_delete_running_project(api):
    import threading

    c, manager, _ = api
    gate = threading.Event()
    real = manager.steps.fetch_post
    manager.steps.fetch_post = lambda url: (gate.wait(5), real(url))[1]
    job = c.post("/api/jobs", json={"url": A, "mode": "load"}).json()
    wait_stage(manager, job["id"], "scraping")
    assert c.delete(f"/api/jobs/{job['id']}").status_code == 400
    gate.set()
    wait_stage(manager, job["id"], "loaded")


def test_launcher_no_longer_deletes_old_projects():
    import inspect

    from app import launcher

    assert "cleanup_old(" not in inspect.getsource(launcher)


def test_prompts_explain_post_boundaries():
    from app.ai.analyze import SYSTEM as A_SYS
    from app.script import SYSTEM as S_SYS

    for sys_prompt in (A_SYS, S_SYS):
        assert "[2번째 글]" in sys_prompt and "경계" in sys_prompt
