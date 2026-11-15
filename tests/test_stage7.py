"""7단계: 영상 프리셋 4종, 1:1 출력, 바뀐 장면만 다시 만들기(목소리·장면 조각 재사용)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import render as rd
from app.captions import render_caption_png
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import render_video, size_for
from app.tts import synthesize_all
from app.visuals import PRESETS, TEMPLATES, letterbox_png
from tests.conftest import make_silence
from tests.test_travel import _duration, _frame


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))


# ---------- 프리셋 ----------
def test_four_presets_use_known_options():
    assert list(PRESETS) == ["cinematic", "vlog", "info", "essay"]
    assert [p["label"] for p in PRESETS.values()] == ["시네마틱 여행 다큐", "감성 브이로그", "정보 전달형", "사진 중심 에세이"]
    for p in PRESETS.values():
        RenderOptions(**p["options"])                  # 값이 모두 올바름
        assert p["options"]["template"] in TEMPLATES and p["help"]


def test_presets_endpoint():
    from app.jobs import JobManager
    from app.main import create_app
    from tests.fakes import fake_steps

    m = JobManager(Path(__import__("tempfile").mkdtemp()) / "jobs", fake_steps())
    c = TestClient(create_app(m), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    r = c.get("/api/presets").json()["presets"]
    assert [p["key"] for p in r] == ["cinematic", "vlog", "info", "essay"] and r[0]["options"]["template"] == "cinema"
    m.stop()


def test_new_templates_have_their_own_look():
    assert TEMPLATES["cinema"].letterbox and not TEMPLATES["essay"].letterbox
    assert TEMPLATES["essay"].extra_hold > 0 and TEMPLATES["essay"].caption == "minimal"
    RenderOptions(template="cinema", preset="cinematic")
    with pytest.raises(Exception):
        RenderOptions(template="movie")


def test_minimal_caption_is_smaller(tmp_path):
    def ink_rows(key):
        im = Image.open(render_caption_png("작은 자막이에요", 1280, 720, tmp_path / f"{key}.png", key))
        alpha = im.getchannel("A").point(lambda a: 255 if a > 200 else 0)
        box = alpha.getbbox()
        return box[3] - box[1]
    assert ink_rows("essay") < ink_rows("mood")


def test_letterbox_only_for_wide_video(tmp_path):
    p = letterbox_png(1280, 720, tmp_path / "lb.png")
    im = Image.open(p)
    assert im.getpixel((640, 5))[3] == 255 and im.getpixel((640, 360))[3] == 0 and im.getpixel((640, 715))[3] == 255
    assert letterbox_png(720, 1280, tmp_path / "v.png") is None and letterbox_png(720, 720, tmp_path / "s.png") is None


# ---------- 1:1 ----------
def test_square_size():
    assert size_for("1:1", "1080p") == (1080, 1080) and size_for("1:1", "720p") == (720, 720)
    assert size_for("16:9", "1080p") == (1920, 1080) and size_for("9:16", "720p") == (720, 1280)


@pytest.fixture
def trip(tmp_path):
    imgs = []
    for i, color in enumerate(["red", "blue", "green"]):
        p = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (800, 600), color).save(p)
        imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
    post = BlogPost(title="통영", paragraphs=["p"], images=imgs, url="u")
    scenes = [Scene(text=f"장면 {i}", image_index=i, duration=1.4,
                    audio_path=str(make_silence(tmp_path / f"v{i}.mp3", 1.0))) for i in range(3)]
    return post, VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[], intro_title="통영 여행")


def _size(path):
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"]


def test_square_video_renders(tmp_path, trip):
    post, script = trip
    out = render_video(post, script, RenderOptions(aspect="1:1", quality="720p"), tmp_path / "w")
    assert _size(out) == (720, 720)


def test_cinema_preset_draws_bars_and_essay_lingers(tmp_path, trip):
    post, script = trip
    opts = dict(quality="720p", intro=False, outro=False, title_bar=False)
    cin = render_video(post, script, RenderOptions(template="cinema", **opts), tmp_path / "c")
    f = _frame(cin, 0.7, 1280, 720)
    assert f[20, 640].sum() < 30 and f[360, 640][0] > 150             # 위는 검은 띠, 가운데는 사진(빨강)
    essay = render_video(post, script, RenderOptions(template="essay", **opts), tmp_path / "e")
    assert abs(_duration(essay) - (3 * 1.4 + 3 * 0.8)) < 0.3            # 장면마다 0.8초 더 머무름


# ---------- 바뀐 장면만 다시 만들기 ----------
def test_rerender_reuses_unchanged_scene_clips(tmp_path, trip, monkeypatch):
    post, script = trip
    opts = RenderOptions(quality="720p", intro=False, outro=False)
    made = []
    real = rd._segment_clip
    monkeypatch.setattr(rd, "_segment_clip", lambda *a, **k: made.append(a[0]) or real(*a, **k))
    work = tmp_path / "w"
    render_video(post, script, opts, work)
    assert len(made) == 3 and json.loads((work / "stats.json").read_text())["reused"] == 0
    made.clear()
    changed = script.model_copy(update={"scenes": [script.scenes[0], script.scenes[1].model_copy(
        update={"text": "고친 장면"}), script.scenes[2]]})
    out = render_video(post, changed, opts, work)
    assert made == [1]                                                  # 고친 장면만 새로 만듦
    assert json.loads((work / "stats.json").read_text()) == {"clips": 3, "reused": 2}
    assert abs(_duration(out) - 4.2) < 0.3
    assert len(list((work / "cache").glob("seg_*.mp4"))) == 3           # 예전 조각은 지움
    made.clear()
    render_video(post, changed, opts.model_copy(update={"template": "vlog"}), work)
    assert len(made) == 3                                               # 스타일을 바꾸면 전부 새로


def test_photo_change_invalidates_clip(tmp_path, trip, monkeypatch):
    post, script = trip
    opts = RenderOptions(quality="720p", intro=False, outro=False)
    work = tmp_path / "w"
    render_video(post, script, opts, work)
    made = []
    real = rd._segment_clip
    monkeypatch.setattr(rd, "_segment_clip", lambda *a, **k: made.append(a[0]) or real(*a, **k))
    Image.new("RGB", (800, 600), "yellow").save(post.images[2].local_path)   # 사진 파일이 바뀜
    import os
    import time
    os.utime(post.images[2].local_path, (time.time() + 5, time.time() + 5))
    render_video(post, script, opts, work)
    assert made == [2]


async def _fake_synth(text, voice, out, **kw):
    make_silence(Path(out), 1.0)


def test_tts_reuses_unchanged_voices(tmp_path):
    calls = []

    async def synth(text, voice, out, **kw):
        calls.append(text)
        make_silence(Path(out), 1.0)

    s = VideoScript(scenes=[Scene(text="하나", image_index=0), Scene(text="둘", image_index=0),
                            Scene(text="하나", image_index=0)], yt_title="t", yt_description="d", yt_tags=[])
    a = synthesize_all(s, "female", tmp_path, synth=synth)
    assert sorted(calls) == ["둘", "하나"]                                  # 같은 글은 한 번만
    assert a.scenes[0].audio_path == a.scenes[2].audio_path
    calls.clear()
    s2 = s.model_copy(update={"scenes": [s.scenes[0], Scene(text="둘을 고침", image_index=0), s.scenes[2]]})
    b = synthesize_all(s2, "female", tmp_path, synth=synth)
    assert calls == ["둘을 고침"] and all(x.duration for x in b.scenes)
    assert len(list(tmp_path.glob("voice_*.mp3"))) == 2                  # 안 쓰는 예전 목소리는 지움
    calls.clear()
    synthesize_all(s2, "female", tmp_path, synth=synth, speed="fast")
    assert len(calls) == 2                                              # 속도가 바뀌면 새로


def test_broken_voice_file_is_made_again(tmp_path):
    calls = []

    async def synth(text, voice, out, **kw):
        calls.append(text)
        make_silence(Path(out), 1.0)

    s = VideoScript(scenes=[Scene(text="하나", image_index=0)], yt_title="t", yt_description="d", yt_tags=[])
    a = synthesize_all(s, "female", tmp_path, synth=synth)
    Path(a.scenes[0].audio_path).write_bytes(b"broken")
    synthesize_all(s, "female", tmp_path, synth=synth)
    assert calls == ["하나", "하나"]


def test_job_records_render_info(tmp_path):
    from app.jobs import JobManager
    from app.main import create_app
    from tests.fakes import fake_steps, render_job, wait_stage

    steps = fake_steps()
    real = steps.render_video

    def render(post, script, options, workdir, on_progress=None):
        out = real(post, script, options, workdir, on_progress)
        (workdir / "stats.json").write_text('{"clips": 5, "reused": 4}', "utf-8")
        return out

    steps.render_video = render
    m = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(m), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "api"}).json()
    wait_stage(m, job["id"], "ready")
    render_job(c, job["id"])
    s = wait_stage(m, job["id"], "done")
    assert s.render_info["clips"] == 5 and s.render_info["reused"] == 4 and s.render_info["seconds"] >= 0
    m.stop()
