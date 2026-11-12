"""여행 영상 기능: 스타일 3종, 시작·끝 화면, 제목 띠·장소 이름표, 전환·움직임, 썸네일."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.captions import render_caption_png
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import INTRO_SEC, OUTRO_SEC, _motion_chain, render_video
from app.visuals import TEMPLATES, render_card_png, render_overlay_png, render_thumbnail
from tests.conftest import make_silence
from tests.test_render_quality import _patch_motion, _textured


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def _frame(video: Path, at: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


@pytest.fixture
def trip(tmp_path):
    imgs = []
    for i, color in enumerate(["red", "blue", "green"]):
        p = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (800, 600), color).save(p)
        imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
    post = BlogPost(title="통영 여행", paragraphs=["p"], images=imgs, url="u", blog_name="여행 블로그")
    scenes = [Scene(text=f"장면 {i} 자막", image_index=i, place="동피랑" if i == 1 else "", duration=1.4,
                    audio_path=str(make_silence(tmp_path / f"v{i}.mp3", 1.0))) for i in range(3)]
    script = VideoScript(scenes=scenes, yt_title="통영 1박 2일", yt_description="d", yt_tags=[],
                         intro_title="통영 1박 2일 여행", intro_sub="가을 · 친구와")
    return post, script


def test_options_defaults():
    o = RenderOptions()
    assert (o.template, o.effects, o.intro, o.outro, o.title_bar, o.channel) == ("clean", "auto", True, True, True, "")
    with pytest.raises(Exception):
        RenderOptions(template="fancy")


def test_old_saved_script_still_loads():
    s = VideoScript.model_validate({"scenes": [{"text": "a", "image_index": 0}], "yt_title": "t",
                                    "yt_description": "d", "yt_tags": []})
    assert s.intro_title == "" and s.scenes[0].place == ""


@pytest.mark.parametrize("key", list(TEMPLATES))
def test_caption_styles_draw_something(tmp_path, key):
    im = Image.open(render_caption_png("자막 테스트", 1280, 720, tmp_path / f"{key}.png", key))
    assert im.size == (1280, 720) and im.mode == "RGBA" and im.getbbox() is not None


def test_caption_styles_differ(tmp_path):
    data = {k: Image.open(render_caption_png("자막", 640, 360, tmp_path / f"{k}.png", k)).tobytes() for k in TEMPLATES}
    assert len(set(data.values())) == len(TEMPLATES)


def test_overlay_title_bar_and_place(tmp_path):
    both = Image.open(render_overlay_png("통영 여행", "동피랑", 1280, 720, tmp_path / "a.png"))
    only_title = Image.open(render_overlay_png("통영 여행", "", 1280, 720, tmp_path / "b.png"))
    empty = Image.open(render_overlay_png("", "", 1280, 720, tmp_path / "c.png"))
    assert empty.getbbox() is None
    assert both.getbbox()[3] > only_title.getbbox()[3]          # 장소 이름표가 띠 아래에 추가됨
    assert both.getbbox()[0] == 0 and both.getbbox()[3] < 720 * 0.35  # 왼쪽 위에만


def test_overlay_long_title_stays_inside(tmp_path):
    im = Image.open(render_overlay_png("아주 " * 30 + "긴 제목", "정말 " * 20 + "긴 장소", 720, 1280, tmp_path / "o.png"))
    assert im.getbbox()[2] <= 720 * 0.75


def test_card_and_emoji_text(tmp_path):
    im = Image.open(render_card_png("outro", "감사합니다 🎉", "구독 부탁드려요", "채널", 1280, 720, tmp_path / "k.png", "vlog"))
    assert im.size == (1280, 720)


def test_thumbnail_size(tmp_path, trip):
    post, _ = trip
    out = render_thumbnail(post.images[0].local_path, "통영 1박 2일 여행 동피랑부터 중앙시장까지", "채널", tmp_path / "t.jpg")
    assert Image.open(out).size == (1280, 720)
    assert Image.open(render_thumbnail(None, "제목", "", tmp_path / "t2.jpg")).size == (1280, 720)


def test_intro_and_outro_add_time(tmp_path, trip):
    post, script = trip
    plain = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False), tmp_path / "a")
    full = render_video(post, script, RenderOptions(quality="720p"), tmp_path / "b")
    assert abs(_duration(plain) - 4.2) < 0.3
    assert abs(_duration(full) - (4.2 + INTRO_SEC + OUTRO_SEC)) < 0.3


def test_intro_shows_card_not_plain_photo(tmp_path, trip):
    post, script = trip
    out = render_video(post, script, RenderOptions(quality="720p"), tmp_path / "w")
    f = _frame(out, 1.5, 1280, 720)
    assert f[300:420, 300:980].max() > 200      # 가운데에 흰 제목 글자
    assert f[40, 1200].sum() < 200              # 사진(빨강)이 어둡게 눌려 있음


def test_title_bar_drawn_on_scenes(tmp_path, trip):
    post, script = trip
    on = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False), tmp_path / "on")
    off = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False, title_bar=False),
                       tmp_path / "off")
    a, b = _frame(on, 0.8, 1280, 720), _frame(off, 0.8, 1280, 720)
    assert np.abs(a[40:90, 0:120] - b[40:90, 0:120]).mean() > 30   # 왼쪽 위 띠
    assert np.abs(a[300:400, 900:1200] - b[300:400, 900:1200]).mean() < 6


def test_transition_blends_two_scenes(tmp_path, trip):
    post, script = trip
    out = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False, title_bar=False,
                                                   effects="soft"), tmp_path / "w")
    r, g, b = _frame(out, 1.4 + 0.3, 1280, 720)[200, 640]   # 둘째 장면 시작 직후: 빨강→파랑 전환 한가운데
    assert r > 40 and b > 40


def test_thumbnail_saved_next_to_video(tmp_path, trip):
    post, script = trip
    out = render_video(post, script, RenderOptions(quality="720p", channel="내 채널"), tmp_path / "w")
    assert Image.open(out.parent / "thumbnail.jpg").size == (1280, 720)


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_every_template_renders(tmp_path, trip, template):
    post, script = trip
    out = render_video(post, script, RenderOptions(quality="720p", aspect="9:16", template=template), tmp_path / "w")
    assert out.exists() and _duration(out) > 10


def test_no_zoompan_in_any_motion():
    for m in ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"):
        assert "zoompan" not in _motion_chain(m, 3.0, (1280, 720))


def test_pan_moves_smoothly(tmp_path):
    """좌우 이동도 떨리지 않아야 한다: 프레임마다 같은 방향으로 고르게 움직임."""
    img = _textured(tmp_path / "tex.jpg")
    post = BlogPost(title="t", paragraphs=[], url="u", images=[ImageRef(url="i", local_path=str(img))])
    audio = make_silence(tmp_path / "a.mp3", 2.6)
    scenes = [Scene(text="", image_index=0, duration=3.0, audio_path=str(audio)) for _ in range(2)]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False, title_bar=False),
                       tmp_path / "w")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "3.7", "-i", str(out), "-frames:v", "50",
                          "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 720, 1280).astype(np.float32)
    first = f[0][200:456, 400:656]
    moved = []   # 첫 프레임에서 얼마나 옮겨 갔는지 (이웃 프레임끼리 재면 1.4픽셀 이동이 1·2로 흔들려 보인다)
    for frame in f:
        a = frame[200:456, 400:656]
        R = np.fft.fft2(a - a.mean()) * np.conj(np.fft.fft2(first - first.mean()))
        r = np.fft.ifft2(R / (np.abs(R) + 1e-9)).real
        _, x = np.unravel_index(np.argmax(r), r.shape)
        moved.append(x - 256 if x > 128 else x)
    moved = np.array(moved, dtype=float)
    t = np.arange(len(moved))
    slope, start = np.polyfit(t, moved, 1)
    assert slope < -0.5                      # 둘째 장면은 오른쪽으로 이동(화면 내용은 왼쪽으로 흐름)
    assert np.abs(moved - (slope * t + start)).max() < 1.0, moved.tolist()   # 고른 속도, 튀는 프레임 없음


# ---------- 채널 이름·썸네일 API ----------
def _client(tmp_path, monkeypatch, steps=None):
    from fastapi.testclient import TestClient

    from app.jobs import JobManager
    from app.main import create_app
    from tests.fakes import fake_steps

    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    manager = JobManager(tmp_path / "jobs", steps or fake_steps())
    return TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000)), manager


def test_channel_name_setting_roundtrip(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    assert c.get("/api/settings").json()["channel_name"] == ""
    r = c.put("/api/settings", json={"channel_name": "  신효의   여행노트 "})
    assert r.json()["channel_name"] == "신효의 여행노트"
    assert c.put("/api/settings", json={"anthropic_api_key": "sk-ant-abcdefgh1234"}).json()["channel_name"] == "신효의 여행노트"
    assert c.put("/api/settings", json={"channel_name": ""}).json()["channel_name"] == ""


def test_render_gets_channel_from_settings_or_blog_name(tmp_path, monkeypatch):
    from tests.fakes import fake_steps, wait_stage

    seen = []
    steps = fake_steps()
    real_render, real_fetch = steps.render_video, steps.fetch_post
    steps.render_video = lambda post, script, options, workdir, on_progress=None: (
        seen.append(options.channel), real_render(post, script, options, workdir, on_progress))[1]
    steps.fetch_post = lambda url: real_fetch(url).model_copy(update={"blog_name": "블로그 이름"})
    name = {"v": ""}
    steps.channel_name = lambda: name["v"]
    c, manager = _client(tmp_path, monkeypatch, steps)
    for expected in ("블로그 이름", "내 채널"):
        job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "auto"}).json()
        wait_stage(manager, job["id"], "ready")
        c.post(f"/api/jobs/{job['id']}/render")
        wait_stage(manager, job["id"], "done")
        assert seen[-1] == expected
        name["v"] = "내 채널"


def test_thumbnail_endpoint(tmp_path, monkeypatch):
    from tests.fakes import wait_stage

    c, manager = _client(tmp_path, monkeypatch)
    job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "auto",
                                    "options": {"template": "vlog", "effects": "soft"}}).json()
    assert job["options"]["template"] == "vlog"
    wait_stage(manager, job["id"], "ready")
    c.post(f"/api/jobs/{job['id']}/render")
    s = wait_stage(manager, job["id"], "done")
    assert c.get(f"/api/jobs/{job['id']}").json()["has_thumbnail"] is False
    assert c.get(f"/api/jobs/{job['id']}/thumbnail").status_code == 404
    Image.new("RGB", (1280, 720), "white").save(Path(s.video_path).parent / "thumbnail.jpg")
    assert c.get(f"/api/jobs/{job['id']}").json()["has_thumbnail"] is True
    r = c.get(f"/api/jobs/{job['id']}/thumbnail?download=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "attachment" in r.headers["content-disposition"]
