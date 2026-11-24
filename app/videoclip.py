"""사용자가 직접 올린 영상 (2026-11-24): 정보 읽기, 쓸 구간 계산, 화면용 작은 그림, 소리 섞기."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.models import Scene, VideoRef
from app.paths import ffmpeg_bin, ffprobe_bin
from app.procutil import quiet_kwargs

MIN_CLIP = 0.5                                   # 영상 장면은 적어도 이만큼
SOUND_VOLUME = {"low": 0.25, "off": 0.0, "full": 1.0}
VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".3gp", ".mts", ".m2ts")


def probe_video(path: Path) -> dict:
    """영상 길이·소리 여부. 영상이 아니면 ValueError."""
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", **quiet_kwargs())
    if r.returncode != 0:
        raise ValueError("not a video")
    try:
        d = json.loads(r.stdout or "{}")
        streams = d.get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video" and s.get("width"))
        duration = float(d.get("format", {}).get("duration") or 0)
    except (StopIteration, ValueError, TypeError) as e:
        raise ValueError("not a video") from e
    if duration <= 0.1:
        raise ValueError("not a video")   # 사진 한 장(jpg 등)도 '영상 스트림'으로 읽히므로 길이로 거른다
    return {"duration": round(duration, 3), "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "width": int(video["width"]), "height": int(video["height"])}


def clip_range(scene: Scene, ref: VideoRef) -> tuple[float, float]:
    """(시작, 길이). 끝이 0이거나 시작보다 앞이거나 영상보다 길면 영상 끝까지."""
    total = max(MIN_CLIP, ref.duration)
    start = min(scene.video_start, total - MIN_CLIP)
    end = scene.video_end if start < scene.video_end <= total else total
    return round(start, 3), round(max(MIN_CLIP, end - start), 3)


def make_thumb(video: Path, out: Path, at: float = 0.0) -> Path:
    """화면에 보여 줄 작은 그림 (영상의 한 장면)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.jpg")
    for t in (at, 0.0):
        subprocess.run([ffmpeg_bin(), "-nostdin", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(tmp)],
                       capture_output=True, **quiet_kwargs())
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(out)
            return out
    raise ValueError("no frame")


def mix_audio(voice: str | None, ref: VideoRef, start: float, length: float, sound: str, out: Path,
              log: Path) -> Path | None:
    """목소리 + 영상 원래 소리(고른 크기)를 장면 길이만큼 섞는다. 둘 다 없으면 None(무음)."""
    volume = SOUND_VOLUME.get(sound, SOUND_VOLUME["low"]) if ref.has_audio else 0.0
    if volume <= 0:
        return Path(voice) if voice else None
    from app.render import _run

    args = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", ref.local_path]
    graph = (f"[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={volume},"
             f"apad,atrim=0:{length:.3f}[clip]")
    if voice:
        args += ["-i", voice]
        graph += (f";[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
                  f"atrim=0:{length:.3f}[voice];[clip][voice]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        graph += ";[clip]anull[a]"
    tmp = out.with_name(out.stem + ".part.wav")
    _run([*args, "-filter_complex", graph, "-map", "[a]", "-t", f"{length:.3f}", "-c:a", "pcm_s16le", str(tmp)], log)
    tmp.replace(out)
    return out
