"""유튜브 링크의 소리를 배경음악(MP3)으로 가져오기 (yt-dlp).

사용이 허락된 음악(직접 만든 곡, 유튜브 오디오 보관함, 저작권 없는 음악 등)에만 쓰도록 화면에서 안내한다.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from app.errors import UserError
from app.paths import ffmpeg_bin

log = logging.getLogger(__name__)
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_SECONDS = 30 * 60
BAD_LINK = "유튜브 영상 주소를 넣어 주세요. (예: https://www.youtube.com/watch?v=...)"
TOO_LONG = "30분이 넘는 영상은 배경음악으로 가져올 수 없어요. 더 짧은 영상을 골라 주세요."
LIVE = "라이브 방송은 배경음악으로 가져올 수 없어요. 일반 영상 주소를 넣어 주세요."
REFUSED = ("유튜브가 지금 이 영상의 소리 내려받기를 막았어요. 잠시 뒤 다시 해 보거나, 음악 파일(MP3)을 따로 받아 "
           "\"내 MP3 파일\"로 넣어 주세요.")
FAILED = "유튜브에서 음악을 가져오지 못했어요. 주소가 맞는지, 볼 수 있는 영상인지 확인해 주세요."


def normalize(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    u = urlparse(url)
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in HOSTS:
        raise UserError(BAD_LINK)
    return url


def safe_name(title: str) -> str:
    """배경음악 파일 이름 규칙(경로 문자 없음, 점으로 시작하지 않음)에 맞춘다."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title or "")
    name = " ".join(name.split()).strip(". ")[:60].strip()
    return (name or "유튜브 음악") + ".mp3"


_env_lock = threading.Lock()


def usable_path(value: str) -> str:
    """PATH에서 통과할 수 없는 폴더를 뺀다.

    yt-dlp는 보조 프로그램을 찾으려고 PATH의 모든 폴더를 os.path.realpath로 풀어 보는데, 윈도우에서
    관리자 권한으로 실행된 프로그램은 일반 사용자가 만든 연결 폴더(junction)를 통과하지 못해
    OSError(WinError 448)가 나고 음악 가져오기 전체가 실패했다(설치 직후 "지금 실행하기"로 켠 경우).
    """
    kept = []
    for item in value.split(os.pathsep):
        if not item:
            continue
        try:
            os.path.realpath(item)
        except OSError:
            log.info("skipping unusable PATH entry: %s", item)
            continue
        kept.append(item)
    return os.pathsep.join(kept)


@contextlib.contextmanager
def _clean_path():
    with _env_lock:
        old = os.environ.get("PATH")
        if old is not None:
            os.environ["PATH"] = usable_path(old)
        try:
            yield
        finally:
            if old is not None:
                os.environ["PATH"] = old


def fetch_audio(url: str, dest: Path) -> str:
    """유튜브 영상의 소리를 dest 폴더에 MP3로 저장하고 파일 이름을 돌려준다."""
    import yt_dlp

    url = normalize(url)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "_yt_download"
    opts = {
        "format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
        "outtmpl": str(tmp) + ".%(ext)s", "overwrites": True, "socket_timeout": 30, "retries": 2,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    ff = Path(ffmpeg_bin())
    if ff.is_file():
        opts["ffmpeg_location"] = str(ff.parent)
    try:
        with _clean_path(), yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
            if info.get("is_live"):
                raise UserError(LIVE)
            if (info.get("duration") or 0) > MAX_SECONDS:
                raise UserError(TOO_LONG)
            y.process_ie_result(info, download=True)
    except UserError:
        raise
    except Exception as e:
        log.warning("youtube music failed: %s: %s", type(e).__name__, e, exc_info=e)
        text = str(e).lower()
        if "live" in text:
            raise UserError(LIVE) from e
        if "403" in text or "forbidden" in text or "sign in to confirm" in text:
            raise UserError(REFUSED) from e   # 유튜브 쪽 차단: 우회하지 않고 다른 방법(파일 직접 넣기)을 안내한다
        raise UserError(FAILED) from e
    made = tmp.with_suffix(".mp3")
    if not made.exists():
        raise UserError(FAILED)
    name = safe_name(info.get("title") or "")
    made.replace(dest / name)
    return name
