"""장면 → MP4 (FFmpeg)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from app.captions import render_caption_png
from app.errors import UserError
from app.models import BlogPost, RenderOptions, VideoScript
from app.paths import ffmpeg_bin, music_dirs
from app.procutil import quiet_kwargs

FPS = 30
FADE = 0.3
BG_COLOR = "0x1e1e28"
FAILED = "영상을 만들지 못했어요."
_SHORT_SIDE = {"720p": 720, "1080p": 1080, "1440p": 1440}


def size_for(aspect: str, quality: str) -> tuple[int, int]:
    short = _SHORT_SIDE[quality]
    long = short * 16 // 9
    return (long, short) if aspect == "16:9" else (short, long)


def resolve_bgm(name: str | None) -> Path | None:
    """음악 폴더(내장/사용자) 안의 파일만 찾는다. 경로가 들어오면 무시."""
    if not name or Path(name).name != name:
        return None
    for d in music_dirs():
        cand = d / name
        if cand.is_file() and cand.resolve().parent == d.resolve():
            return cand
    return None


def _run(args: list[str], log: Path) -> None:
    r = subprocess.run([ffmpeg_bin(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **quiet_kwargs())
    if r.returncode != 0:
        with log.open("a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n" + r.stderr + "\n")
        raise UserError(FAILED)


SUPERSAMPLE = 2   # 확대 효과를 영상의 2배 크기에서 계산한 뒤 줄여서 떨림을 없앤다
ZOOM = 0.10       # 장면 동안 10% 천천히 확대


def _prepare_bg(src: str, out: Path, size: tuple[int, int]) -> Path:
    """사진을 영상 비율에 맞게 꽉 채워 자르고, 2배 크기로 맞춰 저장 (EXIF 회전 반영)."""
    w, h = size
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        ImageOps.fit(img, (w * SUPERSAMPLE, h * SUPERSAMPLE), Image.LANCZOS).save(out, quality=92)
    return out


def _scene_clip(i: int, bg: Path | None, caption: Path, audio: str, duration: float,
                size: tuple[int, int], workdir: Path, log: Path) -> Path:
    w, h = size
    sw, sh = w * SUPERSAMPLE, h * SUPERSAMPLE
    if bg is not None:
        # 매 프레임 크기를 조금씩 키우고(부분 픽셀까지 부드럽게) 가운데를 잘라 원래 크기로 줄인다.
        # 예전 zoompan은 위치를 정수 픽셀로 반올림해서 화면이 1~2픽셀씩 떨렸다.
        src = ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(bg)]
        bg_chain = (f"[0:v]scale=w='trunc({sw}*(1+{ZOOM}*t/{duration:.3f})/2)*2':h=-2:eval=frame:flags=bicubic,"
                    f"crop={sw}:{sh},scale={w}:{h}:flags=lanczos,setsar=1[bg];")
    else:
        src = ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"color=c={BG_COLOR}:s={w}x{h}:r={FPS}"]
        bg_chain = "[0:v]setsar=1[bg];"
    vf = (bg_chain +
          f"[bg][1:v]overlay=0:0,fade=t=in:st=0:d={FADE},"
          f"fade=t=out:st={max(0.0, duration - FADE):.3f}:d={FADE},format=yuv420p[v];"
          f"[2:a]aresample=44100,aformat=channel_layouts=stereo,apad[a]")
    out = workdir / f"scene_{i:02d}.mp4"
    _run([*src, "-loop", "1", "-framerate", str(FPS), "-i", str(caption), "-i", audio,
          "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}", "-r", str(FPS),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", str(out)], log)
    return out


def render_video(post: BlogPost, script: VideoScript, options: RenderOptions, workdir: Path,
                 on_progress: Callable[[float], None] | None = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    log = workdir / "render.log"
    size = size_for(options.aspect, options.quality)
    report = on_progress or (lambda _: None)
    clips = []
    n = len(script.scenes)
    if n == 0:
        raise UserError(FAILED)
    for i, scene in enumerate(script.scenes):
        if not scene.audio_path or not scene.duration:
            raise UserError(FAILED)
        bg = None
        if scene.image_index is not None and 0 <= scene.image_index < len(post.images):
            src = post.images[scene.image_index].local_path
            if src and Path(src).exists():
                try:
                    bg = _prepare_bg(src, workdir / f"bg_{i:02d}.jpg", size)
                except OSError:
                    bg = None
        caption = render_caption_png(scene.text, *size, workdir / f"caption_{i:02d}.png")
        clips.append(_scene_clip(i, bg, caption, scene.audio_path, scene.duration, size, workdir, log))
        report(0.9 * (i + 1) / n)

    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), "utf-8")
    total = sum(s.duration for s in script.scenes)
    out = workdir / "output.mp4"
    args = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    bgm = resolve_bgm(options.bgm)
    if bgm:
        args += ["-stream_loop", "-1", "-i", str(bgm), "-filter_complex",
                 f"[1:a]aresample=44100,aformat=channel_layouts=stereo,volume={options.bgm_volume},"
                 f"atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 2):.3f}:d=2[b];"
                 f"[0:a][b]amix=inputs=2:duration=first:normalize=0[a]",
                 "-map", "0:v", "-map", "[a]"]
    else:
        args += ["-map", "0:v", "-map", "0:a"]
    _run([*args, "-t", f"{total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-movflags", "+faststart", str(out)], log)
    report(1.0)
    return out
