"""유튜브 링크의 소리를 배경음악(MP3)으로 가져오기 (yt-dlp).

사용이 허락된 음악(직접 만든 곡, 유튜브 오디오 보관함, 저작권 없는 음악 등)에만 쓰도록 화면에서 안내한다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from app.errors import UserError
from app.paths import ffmpeg_bin

log = logging.getLogger(__name__)
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_SECONDS = 30 * 60
BAD_LINK = "유튜브 영상 주소를 넣어 주세요. (예: https://www.youtube.com/watch?v=...)"
TOO_LONG = "30분이 넘는 영상은 배경음악으로 가져올 수 없어요. 더 짧은 영상을 골라 주세요."
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
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
            if info.get("is_live") or (info.get("duration") or 0) > MAX_SECONDS:
                raise UserError(TOO_LONG)
            y.process_ie_result(info, download=True)
    except UserError:
        raise
    except Exception as e:
        log.warning("youtube music failed: %s: %s", type(e).__name__, e)
        raise UserError(FAILED) from e
    made = tmp.with_suffix(".mp3")
    if not made.exists():
        raise UserError(FAILED)
    name = safe_name(info.get("title") or "")
    made.replace(dest / name)
    return name
