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

FPS = 30
FADE = 0.3
BG_COLOR = "0x1e1e28"
FAILED = "영상을 만들지 못했어요."
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}


def resolve_bgm(name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p
    for d in music_dirs():
        cand = d / Path(name).name
        if cand.exists():
            return cand
    return None


def _run(args: list[str], log: Path) -> None:
    r = subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        with log.open("a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n" + r.stderr + "\n")
        raise UserError(FAILED)


def _prepare_bg(src: str, out: Path) -> Path:
    with Image.open(src) as im:
        ImageOps.exif_transpose(im).convert("RGB").save(out, quality=92)
    return out


def _scene_clip(i: int, bg: Path | None, caption: Path, audio: str, duration: float,
                size: tuple[int, int], workdir: Path, log: Path) -> Path:
    w, h = size
    bw, bh = int(w * 1.2) // 2 * 2, int(h * 1.2) // 2 * 2
    frames = max(1, round(duration * FPS))
    if bg is not None:
        src = ["-i", str(bg)]
    else:
        src = ["-f", "lavfi", "-i", f"color=c={BG_COLOR}:s={bw}x{bh}:r=1:d=1"]
    vf = (f"[0:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},setsar=1,"
          f"zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={w}x{h}:fps={FPS}[bg];"
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
    size = SIZES[options.aspect]
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
                    bg = _prepare_bg(src, workdir / f"bg_{i:02d}.jpg")
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
