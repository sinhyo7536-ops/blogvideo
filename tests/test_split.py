"""기능 나누기: 블로그 불러오기 → 추천대로 작성(AI가 사진을 보고) / 직접 작성, 장면별 움직임·전환."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import XFADE, _build_segments, _motion_chain
from app.script import SYSTEM, ScriptDraft, SceneDraft, generate_script, photos_for_ai
from tests.conftest import make_silence
from tests.fakes import fake_steps, wait_stage

URL = "https://blog.naver.com/a/1"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    steps = fake_steps()
    calls = {"script": 0}
    real = steps.generate_script

    def counted(post, key):
        calls["script"] += 1
        return real(post, key)

    steps.generate_script = counted
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager, calls
    manager.stop()


def _load(c, manager):
    job = c.post("/api/jobs", json={"url": URL, "mode": "load"}).json()
    wait_stage(manager, job["id"], "loaded")
    return job["id"]


# ---------- 불러오기 ----------
def test_load_only_fetches_post_and_photos(api):
    c, manager, calls = api
    job_id = _load(c, manager)
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["stage"] == "loaded" and job["script"] is None and calls["script"] == 0
    assert job["post"]["title"] and len(job["post"]["images"]) == 1 and job["post"]["paragraphs"]


def test_load_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    steps = fake_steps()
    steps.api_key = lambda: ""
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    job_id = _load(c, manager)
    r = c.post(f"/api/jobs/{job_id}/write", json={"mode": "api"})
    assert r.status_code == 400 and "키" in r.json()["message"]
    assert c.post(f"/api/jobs/{job_id}/write", json={"mode": "manual"}).status_code == 200
    manager.stop()


def test_render_before_writing_is_refused(api):
    c, manager, _ = api
    job_id = _load(c, manager)
    assert c.post(f"/api/jobs/{job_id}/render").status_code == 400


# ---------- 추천대로 작성 ----------
def test_recommended_writes_script_from_loaded_post(api):
    c, manager, calls = api
    job_id = _load(c, manager)
    assert c.post(f"/api/jobs/{job_id}/write", json={"mode": "api"}).status_code == 200
    s = wait_stage(manager, job_id, "ready")
    assert calls["script"] == 1 and s.mode == "api" and s.script.scenes


def test_recommended_failure_keeps_post_and_can_retry_or_go_free(api):
    c, manager, _ = api
    from app.errors import UserError

    state = {"fail": True}
    ok = manager.steps.generate_script

    def flaky(post, key):
        if state["fail"]:
            raise UserError("API 크레딧이 부족해요.")
        return ok(post, key)

    manager.steps.generate_script = flaky
    fetches = {"n": 0}
    real_fetch = manager.steps.fetch_post
    manager.steps.fetch_post = lambda url: (fetches.__setitem__("n", fetches["n"] + 1), real_fetch(url))[1]
    job_id = _load(c, manager)
    c.post(f"/api/jobs/{job_id}/write", json={"mode": "api"})
    s = wait_stage(manager, job_id, "error")
    assert s.failed_phase == "script" and s.post is not None
    state["fail"] = False
    c.post(f"/api/jobs/{job_id}/retry")
    wait_stage(manager, job_id, "ready")
    assert fetches["n"] == 1          # 다시 시도해도 글을 또 받아오지 않음


def test_write_again_replaces_script(api):
    c, manager, _ = api
    job_id = _load(c, manager)
    c.post(f"/api/jobs/{job_id}/write", json={"mode": "manual"})
    c.post(f"/api/jobs/{job_id}/write", json={"mode": "auto"})
    s = wait_stage(manager, job_id, "ready")
    assert s.mode == "auto" and s.script.scenes


# ---------- 직접 작성 ----------
def test_manual_starts_with_empty_scenes_and_filled_info(api):
    c, manager, calls = api
    job_id = _load(c, manager)
    job = c.post(f"/api/jobs/{job_id}/write", json={"mode": "manual"}).json()
    assert job["stage"] == "ready" and job["mode"] == "manual" and calls["script"] == 0
    assert job["script"]["scenes"] == [] and job["script"]["yt_title"] and URL in job["script"]["yt_description"]


def test_manual_scenes_keep_chosen_effects(api):
    c, manager, _ = api
    job_id = _load(c, manager)
    job = c.post(f"/api/jobs/{job_id}/write", json={"mode": "manual"}).json()
    script = job["script"]
    script["scenes"] = [{"text": "직접 쓴 장면", "image_index": 0, "place": "동피랑", "motion": "pan_left",
                         "transition": "circle"}]
    r = c.put(f"/api/jobs/{job_id}/script", json={"script": script, "options": job["options"]})
    scene = r.json()["script"]["scenes"][0]
    assert (scene["motion"], scene["transition"], scene["place"]) == ("pan_left", "circle", "동피랑")
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")


def test_unknown_effect_names_become_auto():
    s = Scene(text="a", image_index=0, motion="spin", transition="explode")
    assert (s.motion, s.transition) == ("auto", "auto")
    assert Scene(text="a", image_index=0).motion == "auto"


# ---------- 화면용 작은 사진 ----------
def test_small_image_is_downsized(api, tmp_path):
    c, manager, _ = api
    job_id = _load(c, manager)
    path = manager.get(job_id).post.images[0].local_path
    Image.new("RGB", (3000, 2000), "teal").save(path)
    r = c.get(f"/api/jobs/{job_id}/image/0?small=1")
    assert r.status_code == 200
    import io

    assert max(Image.open(io.BytesIO(r.content)).size) == 480
    assert len(c.get(f"/api/jobs/{job_id}/image/0").content) > len(r.content)


# ---------- AI가 사진을 본다 ----------
@pytest.fixture
def photo_post(tmp_path):
    imgs = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (2000, 1500), ["red", "green", "blue"][i]).save(p)
        imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
    return BlogPost(title="통영 여행", paragraphs=["첫 문단"], url=URL, images=imgs)


def test_photos_for_ai_are_small_and_numbered(photo_post, tmp_path):
    import io

    photos = photos_for_ai(photo_post)
    assert [i for i, _ in photos] == [0, 1, 2]
    assert max(Image.open(io.BytesIO(photos[0][1])).size) == 768
    many = photo_post.model_copy(update={"images": photo_post.images * 20})
    picked = [i for i, _ in photos_for_ai(many, limit=10)]
    assert len(picked) == 10 and picked[0] == 0 and picked[-1] >= 50   # 처음부터 끝까지 고르게


class FakeClaude:
    def __init__(self, draft):
        self.kwargs = None
        self.draft = draft
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.draft)


DRAFT = ScriptDraft(scenes=[SceneDraft(text="항구예요", image_index=2, place="강구안", motion="pan_right",
                                       transition="slide"),
                            SceneDraft(text="끝", image_index=0, motion="회전", transition="")],
                    yt_title="t", yt_description="d", yt_tags=["a"], intro_title="통영 여행")


def test_claude_gets_photos_with_numbers(photo_post):
    c = FakeClaude(DRAFT)
    s = generate_script(photo_post, "k", client=c)
    content = c.kwargs["messages"][0]["content"]
    images = [b for b in content if b["type"] == "image"]
    labels = [b["text"] for b in content if b["type"] == "text"]
    assert len(images) == 3 and images[0]["source"]["media_type"] == "image/jpeg"
    assert "사진 2번" in labels and URL in labels[0]
    assert (s.scenes[0].image_index, s.scenes[0].motion, s.scenes[0].transition) == (2, "pan_right", "slide")
    assert (s.scenes[1].motion, s.scenes[1].transition) == ("auto", "auto")   # 모르는 값은 자동으로


def test_claude_without_downloaded_photos_gets_text_only():
    post = BlogPost(title="t", paragraphs=["p"], url=URL, images=[ImageRef(url="u")])
    c = FakeClaude(DRAFT)
    generate_script(post, "k", client=c)
    assert isinstance(c.kwargs["messages"][0]["content"], str)


def test_gemini_gets_photos_and_effect_schema(photo_post):
    from app.gemini import SCHEMA, generate_script_gemini

    seen = {}

    def gen(*, model, contents, config):
        seen["contents"] = contents
        return SimpleNamespace(text=json.dumps({
            "scenes": [{"text": "항구예요", "image_index": 1, "place": "", "motion": "zoom_out", "transition": "circle"}],
            "yt_title": "t", "yt_description": "d", "yt_tags": ["a"], "intro_title": "통영", "intro_sub": ""},
            ensure_ascii=False))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    s = generate_script_gemini(photo_post, "k", client=client)
    parts = [p for p in seen["contents"] if not isinstance(p, str)]
    assert len(parts) == 3 and "사진 1번" in seen["contents"]
    assert (s.scenes[0].image_index, s.scenes[0].motion, s.scenes[0].transition) == (1, "zoom_out", "circle")
    scene = SCHEMA["properties"]["scenes"]["items"]
    assert "pan_left" in scene["properties"]["motion"]["enum"] and "circle" in scene["properties"]["transition"]["enum"]


def test_prompt_tells_ai_to_look_at_photos_and_pick_effects():
    assert "사진을 직접 보고" in SYSTEM and "motion" in SYSTEM and "transition" in SYSTEM


# ---------- 장면별 움직임·전환이 영상에 쓰인다 ----------
def test_scene_effects_reach_the_renderer(tmp_path, photo_post):
    audio = str(make_silence(tmp_path / "a.mp3", 1.0))
    scenes = [Scene(text="a", image_index=0, duration=1.4, audio_path=audio, motion="pan_up"),
              Scene(text="b", image_index=1, duration=1.4, audio_path=audio, transition="circle"),
              Scene(text="c", image_index=2, duration=1.4, audio_path=audio, motion="still", transition="zoom")]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    segs = _build_segments(photo_post, script, RenderOptions(quality="720p", intro=False, outro=False),
                           (1280, 720), tmp_path / "w")
    assert [s.motion for s in segs] == ["pan_up", "pan_right", "still"]     # 둘째는 자동(순서대로)
    assert [s.transition for s in segs] == ["", XFADE["circle"], XFADE["zoom"]]


def test_soft_effects_do_not_override_chosen_ones(tmp_path, photo_post):
    audio = str(make_silence(tmp_path / "a.mp3", 1.0))
    scenes = [Scene(text="a", image_index=0, duration=1.4, audio_path=audio, motion="pan_left"),
              Scene(text="b", image_index=1, duration=1.4, audio_path=audio)]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    segs = _build_segments(photo_post, script, RenderOptions(quality="720p", intro=False, outro=False, effects="soft"),
                           (1280, 720), tmp_path / "w")
    assert [s.motion for s in segs] == ["pan_left", "zoom_in"]


def test_every_transition_and_still_motion_render(tmp_path, photo_post):
    from app.models import TRANSITIONS
    from app.render import render_video

    audio = str(make_silence(tmp_path / "a.mp3", 0.6))
    scenes = [Scene(text=f"장면 {i}", image_index=i % 3, duration=1.0, audio_path=audio,
                    motion="still" if i == 0 else "auto", transition=t)
              for i, t in enumerate(("auto",) + TRANSITIONS)]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(photo_post, script, RenderOptions(quality="720p", intro=False, outro=False), tmp_path / "w")
    assert out.exists() and "zoompan" not in _motion_chain("still", 1.0, (1280, 720))


# ---------- 긴 영상: 장면이 많아도 끝까지 만들어진다 ----------
def test_many_scenes_are_joined_in_groups(tmp_path, photo_post):
    import subprocess

    from app.render import GROUP, render_video

    audio = str(make_silence(tmp_path / "a.mp3", 0.5))
    n = GROUP * 2 + 3          # 묶음 3개(마지막은 덜 찬 묶음)
    scenes = [Scene(text=f"장면 {i}", image_index=i % 3, duration=0.9, audio_path=audio) for i in range(n)]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[], intro_title="긴 영상")
    seen = []
    out = render_video(photo_post, script, RenderOptions(quality="720p", effects="soft"), tmp_path / "w",
                       on_progress=seen.append)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - (n * 0.9 + 6.5)) < 0.4          # 시작 3초 + 끝 3.5초 포함, 길이가 줄거나 늘지 않음
    assert len(list((tmp_path / "w").glob("part_0_*.mkv"))) == 3
    assert seen == sorted(seen) and seen[-1] == 1.0
    # 묶음 경계(8번째 장면 → 9번째 장면)도 끊기지 않고 전환됨: 경계 직후 화면이 두 사진의 중간
    at = 3.0 + (GROUP - 1) * 0.9 + 0.2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    import numpy as np

    px = np.frombuffer(raw, np.uint8).reshape(720, 1280, 3)[360, 900].astype(int)
    assert sorted(px)[1] > 20, px.tolist()            # 한 가지 원색이 아니라 섞인 색
