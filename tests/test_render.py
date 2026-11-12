import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.errors import UserError
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import render_video
from tests.conftest import make_silence


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    d = json.loads(out.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    return {"width": v["width"], "height": v["height"], "duration": float(d["format"]["duration"]),
            "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


@pytest.fixture
def inputs(tmp_path):
    def make(with_images=True):
        imgs = []
        if with_images:
            for i, (color, size) in enumerate([("red", (800, 600)), ("blue", (600, 1000))]):
                p = tmp_path / f"img_{i}.jpg"
                Image.new("RGB", size, color).save(p)
                imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
        post = BlogPost(title="t", paragraphs=["p"], images=imgs, url="u")
        scenes = []
        for i in range(2):
            a = make_silence(tmp_path / f"v{i}.mp3", 1.0)
            scenes.append(Scene(text=f"장면 {i} 자막: \"따옴표\" 50%", image_index=i if with_images else None,
                                duration=1.4, audio_path=str(a)))
        return post, VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    return make


@pytest.mark.parametrize("aspect,size", [("16:9", (1920, 1080)), ("9:16", (1080, 1920))])
def test_render_video_dimensions_and_length(tmp_path, inputs, aspect, size):
    post, script = inputs()
    out = render_video(post, script, RenderOptions(aspect=aspect), tmp_path / "w")
    info = probe(out)
    assert out.name == "output.mp4"
    assert (info["width"], info["height"]) == size
    assert abs(info["duration"] - 2.8) < 0.6
    assert info["has_audio"]


def test_render_without_images(tmp_path, inputs):
    post, script = inputs(with_images=False)
    assert probe(render_video(post, script, RenderOptions(), tmp_path / "w"))["width"] == 1920


def test_render_with_bgm(tmp_path, inputs):
    post, script = inputs()
    bgm = make_silence(tmp_path / "bgm.mp3", 0.5)  # 영상보다 짧아도 반복
    info = probe(render_video(post, script, RenderOptions(bgm=str(bgm)), tmp_path / "w"))
    assert abs(info["duration"] - 2.8) < 0.6


def test_render_progress_reaches_one(tmp_path, inputs):
    seen = []
    post, script = inputs()
    render_video(post, script, RenderOptions(), tmp_path / "w", on_progress=seen.append)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)


def test_render_failure_is_user_error_with_log(tmp_path, inputs):
    post, script = inputs()
    script.scenes[0].audio_path = str(tmp_path / "missing.mp3")
    with pytest.raises(UserError, match="영상을 만들지 못했어요"):
        render_video(post, script, RenderOptions(), tmp_path / "w")
    assert (tmp_path / "w" / "render.log").read_text("utf-8")
