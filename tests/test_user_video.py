"""내 영상 넣기 (사용자 요청, 2026-11-24): 블로그에 없는 사진·영상도 편집 화면에서 직접 넣고 자막을 단다.

결정: 장면 길이는 영상 길이대로(시작·끝 초로 직접 자를 수 있음). 목소리가 더 길면 마지막 화면에서 멈춘다.
영상 원래 소리는 장면마다 '작게 / 끄기 / 그대로' 중에서 고른다.
"""
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, RenderOptions, Scene, VideoRef, VideoScript
from app.paths import ffmpeg_bin
from app.render import render_video
from app.tts import synthesize_all
from app.videoclip import clip_range, probe_video
from tests.conftest import make_silence
from tests.fakes import fake_steps, wait_stage
from tests.test_render import probe


def make_clip(path: Path, seconds: float = 2.0, audio: bool = True, size: str = "640x360") -> Path:
    args = [ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s={size}:r=30:d={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds)]
    args += ["-c:a", "aac"] if audio else []
    subprocess.run([*args, str(path)], check=True)
    return path


def mean_volume(path: Path) -> float:
    r = subprocess.run([ffmpeg_bin(), "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -120.0


# ---------- 값 ----------
def test_scene_video_fields_are_sanitized():
    s = Scene(text="", image_index=None, video_index=0, video_start=-3, video_end="x", video_sound="loud")
    assert (s.video_start, s.video_end, s.video_sound) == (0.0, 0.0, "low")
    assert Scene(text="", image_index=None).video_index is None
    for v in ("low", "off", "full"):
        assert Scene(text="", image_index=None, video_sound=v).video_sound == v


def test_clip_range_defaults_to_whole_video_and_can_be_trimmed():
    ref = VideoRef(name="a.mp4", local_path="a.mp4", duration=10.0)
    assert clip_range(Scene(text="", image_index=None, video_index=0), ref) == (0.0, 10.0)
    assert clip_range(Scene(text="", image_index=None, video_index=0, video_start=2, video_end=5), ref) == (2.0, 3.0)
    # 끝이 시작보다 앞이거나 영상보다 길면 영상 끝까지
    assert clip_range(Scene(text="", image_index=None, video_index=0, video_start=4, video_end=2), ref) == (4.0, 6.0)
    assert clip_range(Scene(text="", image_index=None, video_index=0, video_start=8, video_end=30), ref) == (8.0, 2.0)
    # 시작이 영상 길이를 넘으면 끝 0.5초는 남긴다
    assert clip_range(Scene(text="", image_index=None, video_index=0, video_start=50), ref) == (9.5, 0.5)


def test_probe_video(tmp_path):
    info = probe_video(make_clip(tmp_path / "a.mp4", 2.0))
    assert abs(info["duration"] - 2.0) < 0.2 and info["has_audio"] is True
    assert probe_video(make_clip(tmp_path / "b.mp4", 1.0, audio=False))["has_audio"] is False


# ---------- 목소리 없는 장면 ----------
def test_empty_caption_scene_has_no_voice(tmp_path, monkeypatch):
    calls = []

    async def synth(text, voice, out, **kw):
        calls.append(text)
        out.write_bytes(b"x")

    monkeypatch.setattr("app.tts.probe_duration", lambda p: 1.0)
    script = VideoScript(scenes=[Scene(text="  \n ", image_index=None), Scene(text="안녕", image_index=None)],
                         yt_title="t", yt_description="", yt_tags=[])
    out = synthesize_all(script, "female", tmp_path, synth=synth)
    assert calls == ["안녕"]
    assert out.scenes[0].audio_path is None and out.scenes[0].duration == 0.0
    assert out.scenes[1].audio_path


# ---------- 영상 만들기 ----------
def _video_post(tmp_path, seconds=2.0, audio=True):
    clip = make_clip(tmp_path / "clip.mp4", seconds, audio)
    return BlogPost(title="t", paragraphs=["p"], images=[], url="u",
                    videos=[VideoRef(name="clip.mp4", local_path=str(clip), duration=seconds, has_audio=audio)])


def _script(*scenes):
    return VideoScript(scenes=list(scenes), yt_title="t", yt_description="", yt_tags=[])


def test_video_scene_length_follows_video(tmp_path):
    post = _video_post(tmp_path, 2.0)
    voice = make_silence(tmp_path / "v.mp3", 0.5)
    sc = Scene(text="바다 영상\n정말 예뻐요", image_index=None, video_index=0, audio_path=str(voice), duration=0.7)
    out = render_video(post, _script(sc), RenderOptions(intro=False, outro=False, quality="720p"), tmp_path / "w")
    info = probe(out)
    assert (info["width"], info["height"]) == (1280, 720) and info["has_audio"]
    assert abs(info["duration"] - 2.0) < 0.4


def test_voice_longer_than_video_holds_last_frame(tmp_path):
    post = _video_post(tmp_path, 1.0)
    voice = make_silence(tmp_path / "v.mp3", 2.5)
    sc = Scene(text="긴 설명", image_index=None, video_index=0, audio_path=str(voice), duration=2.7)
    out = render_video(post, _script(sc), RenderOptions(intro=False, outro=False, quality="720p"), tmp_path / "w")
    assert abs(probe(out)["duration"] - 2.7) < 0.4


def test_trimmed_video_scene(tmp_path):
    post = _video_post(tmp_path, 3.0)
    sc = Scene(text="", image_index=None, video_index=0, video_start=0.5, video_end=1.5)
    out = render_video(post, _script(sc), RenderOptions(intro=False, outro=False, quality="720p"), tmp_path / "w")
    assert abs(probe(out)["duration"] - 1.0) < 0.35


@pytest.mark.parametrize("sound", ["off", "low", "full"])
def test_video_sound_choice(tmp_path, sound):
    post = _video_post(tmp_path, 1.5)
    sc = Scene(text="", image_index=None, video_index=0, video_sound=sound)
    out = render_video(post, _script(sc), RenderOptions(intro=False, outro=False, quality="720p"), tmp_path / "w")
    vol = mean_volume(out)
    if sound == "off":
        assert vol < -60
    elif sound == "low":            # 시험용 소리(-21dB 정도)를 1/4 크기로
        assert -45 < vol < -28
    else:
        assert vol > -26


def test_video_without_sound_track_and_photo_scene_without_caption(tmp_path):
    post = _video_post(tmp_path, 1.0, audio=False)
    from PIL import Image

    img = tmp_path / "p.jpg"
    Image.new("RGB", (800, 600), "red").save(img)
    from app.models import ImageRef

    post = post.model_copy(update={"images": [ImageRef(url="u", local_path=str(img))]})
    scenes = [Scene(text="", image_index=None, video_index=0, video_sound="full"),
              Scene(text="", image_index=0)]                     # 자막 없는 사진 장면도 만들어진다
    out = render_video(post, _script(*scenes), RenderOptions(intro=False, outro=False, quality="720p"),
                       tmp_path / "w")
    assert probe(out)["duration"] > 2.0


# ---------- 올리기 (편집 화면) ----------
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    m = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(m), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "text"}).json()
    wait_stage(m, job["id"], "ready")
    yield c, m, job["id"]
    m.stop()


def test_upload_video_adds_it_to_the_project(api, tmp_path):
    c, m, jid = api
    clip = make_clip(tmp_path / "내 영상.mp4", 1.5)
    r = c.post(f"/api/jobs/{jid}/videos", files={"file": ("내 영상.mp4", clip.read_bytes(), "video/mp4")})
    assert r.status_code == 200, r.text
    videos = r.json()["post"]["videos"]
    assert len(videos) == 1 and videos[0]["name"] == "내 영상.mp4" and abs(videos[0]["duration"] - 1.5) < 0.2
    assert "local_path" not in videos[0] or Path(videos[0]["local_path"]).parent.name == "videos"
    thumb = c.get(f"/api/jobs/{jid}/videos/0/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    file = c.get(f"/api/jobs/{jid}/videos/0")
    assert file.status_code == 200 and len(file.content) == clip.stat().st_size
    assert c.get(f"/api/jobs/{jid}/videos/5/thumb").status_code == 404


def test_upload_rejects_non_video(api):
    c, m, jid = api
    r = c.post(f"/api/jobs/{jid}/videos", files={"file": ("a.mp4", b"not a video", "video/mp4")})
    assert r.status_code == 400 and "영상" in r.json()["message"]
    assert not m.get(jid).post.videos
