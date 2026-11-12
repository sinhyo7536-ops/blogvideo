import subprocess
from pathlib import Path

import pytest

from app.models import Scene, VideoScript


def make_silence(out: Path, seconds: float = 1.0) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=24000:cl=mono", "-t", str(seconds), "-c:a", "libmp3lame", str(out)], check=True)
    return out


@pytest.fixture
def short_script():
    return VideoScript(scenes=[Scene(text="첫 장면", image_index=0), Scene(text="둘째 장면", image_index=None)],
                       yt_title="t", yt_description="d", yt_tags=[])
