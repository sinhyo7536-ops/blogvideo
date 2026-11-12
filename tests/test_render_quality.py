"""영상 품질: 확대 효과가 떨리지 않을 것, 화질(720p/1080p/1440p) 선택."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import render_video, size_for
from tests.conftest import make_silence


def _textured(path: Path, w=2400, h=1600):
    rng = np.random.default_rng(1)
    noise = (rng.random((h // 8, w // 8)) * 255).astype(np.uint8)
    Image.fromarray(noise).resize((w, h), Image.BICUBIC).convert("RGB").save(path, quality=95)
    return path


def _video(tmp_path, options, seconds=3.0):
    img = _textured(tmp_path / "tex.jpg")
    post = BlogPost(title="t", paragraphs=[], url="u", images=[ImageRef(url="i", local_path=str(img))])
    audio = make_silence(tmp_path / "a.mp3", seconds - 0.4)
    script = VideoScript(scenes=[Scene(text="", image_index=0, duration=seconds, audio_path=str(audio))],
                         yt_title="t", yt_description="d", yt_tags=[])
    return render_video(post, script, options, tmp_path / "w")


def _patch_motion(video: Path, w: int, h: int, n=60):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(video), "-frames:v", str(n),
                          "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)
    s = 128
    y0, x0 = h // 6, w // 8  # 가운데에서 떨어진 곳(확대 시 움직임이 큰 곳)
    moves = []
    for i in range(len(f) - 1):
        a, b = f[i + 1][y0:y0 + s, x0:x0 + s], f[i][y0:y0 + s, x0:x0 + s]
        A, B = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
        R = A * np.conj(B)
        R /= np.abs(R) + 1e-9
        r = np.fft.ifft2(R).real
        y, x = np.unravel_index(np.argmax(r), r.shape)
        x = x - s if x > s / 2 else x
        y = y - s if y > s / 2 else y
        moves.append((x, y))
    return np.array(moves, dtype=float)


def test_zoom_has_no_jitter(tmp_path):
    """정수 픽셀 반올림 때문에 1~2픽셀씩 튀며 떨리던 문제(zoompan)를 막는다."""
    out = _video(tmp_path, RenderOptions(quality="720p"))
    m = _patch_motion(out, 1280, 720)
    # 프레임 사이 이동이 1픽셀 이상 튀는 일이 없어야 함
    assert np.abs(m).max() < 1.0, m[:20].tolist()


@pytest.mark.parametrize("quality,aspect,size", [
    ("720p", "16:9", (1280, 720)), ("1080p", "16:9", (1920, 1080)), ("1440p", "16:9", (2560, 1440)),
    ("720p", "9:16", (720, 1280)), ("1440p", "9:16", (1440, 2560)),
])
def test_size_for(quality, aspect, size):
    assert size_for(aspect, quality) == size


def test_render_uses_selected_quality(tmp_path):
    out = _video(tmp_path, RenderOptions(quality="720p", aspect="9:16"), seconds=1.5)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                      "stream=width,height", "-of", "json", str(out)],
                                     capture_output=True, text=True).stdout)["streams"][0]
    assert (info["width"], info["height"]) == (720, 1280)


def test_quality_default_is_1080p():
    assert RenderOptions().quality == "1080p"
    with pytest.raises(Exception):
        RenderOptions(quality="4k")
