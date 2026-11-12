"""장면 → MP4 (FFmpeg).

순서: 장면마다 소리 없는 영상 조각을 만든다(사진 움직임 + 제목 띠 + 자막) → 조각들을 전환 효과(xfade)로
이어 붙이면서 목소리·배경음악을 얹는다 → 썸네일을 만든다.
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from app import visuals
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

SUPERSAMPLE = 2   # 움직임을 영상의 2배 크기에서 계산한 뒤 줄여서 떨림을 없앤다
ZOOM = 0.10       # 장면 동안 10% 천천히 확대(또는 그만큼 이동)
INTRO_SEC = 3.0
OUTRO_SEC = 3.5
SOFT_SEC = 0.6
CAPTION_IN = 0.35  # 자막이 아래에서 떠오르는 시간
MOTIONS = ("zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "pan_up", "zoom_out", "pan_down")  # 자동일 때 순서
XFADE = {"fade": "fade", "slide": "slideleft", "push": "smoothleft", "circle": "circleopen",
         "black": "fadeblack", "zoom": "zoomin", "wipe": "wiperight"}
THANKS = "시청해 주셔서 감사합니다"
SUBSCRIBE = "구독과 좋아요 부탁드려요"


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


_proc_lock = threading.Lock()
_proc: subprocess.Popen | None = None   # 지금 돌고 있는 FFmpeg (작업은 한 번에 하나)


def stop_current() -> bool:
    """돌고 있는 FFmpeg를 바로 끝낸다("만들기 멈추기"). 끝낸 것이 있으면 True."""
    with _proc_lock:
        p = _proc
    if p is None or p.poll() is not None:
        return False
    try:
        p.kill()
    except OSError:
        return False
    return True


def _run(args: list[str], log: Path) -> None:
    global _proc
    p = subprocess.Popen([ffmpeg_bin(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", **quiet_kwargs())
    with _proc_lock:
        _proc = p
    try:
        _, err = p.communicate()
    finally:
        with _proc_lock:
            _proc = None
    if p.returncode != 0:
        with log.open("a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n" + (err or "") + "\n")
        raise UserError(FAILED)


@dataclass
class Segment:
    """영상의 한 토막 (시작 화면 / 장면 / 끝 화면)."""
    duration: float
    bg: Path | None = None            # 2배 크기로 준비된 배경 사진
    overlays: list[Path] = field(default_factory=list)   # 가만히 떠 있는 요소 (제목 띠, 카드 글자)
    caption: Path | None = None       # 아래에서 떠오르는 자막
    audio: str | None = None          # 없으면 무음
    motion: str = "zoom_in"
    transition: str = ""              # 이 토막으로 들어올 때의 xfade 종류. 비면 스타일의 기본 순서


def _prepare_bg(src: str, out: Path, size: tuple[int, int]) -> Path:
    """사진을 영상 비율에 맞게 꽉 채워 자르고, 2배 크기로 맞춰 저장 (EXIF 회전 반영)."""
    w, h = size
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        ImageOps.fit(img, (w * SUPERSAMPLE, h * SUPERSAMPLE), Image.LANCZOS).save(out, quality=92)
    return out


def _motion_chain(motion: str, length: float, size: tuple[int, int]) -> str:
    """배경 사진의 움직임. zoompan은 정수 픽셀 반올림으로 떨리므로 쓰지 않는다.

    확대/축소: 매 프레임 크기를 조금씩 바꾸고 가운데를 자른다.
    이동: 10% 크게 깔아 두고 자르는 위치를 2배 크기 기준으로 조금씩 옮긴다(줄이면 0.5픽셀 단위로 부드럽다).
    """
    w, h = size
    sw, sh = w * SUPERSAMPLE, h * SUPERSAMPLE
    L = f"{length:.3f}"
    if motion == "still":
        chain = f"scale={sw}:{sh}:flags=bicubic"
    elif motion in ("zoom_in", "zoom_out"):
        grow = f"{ZOOM}*t/{L}" if motion == "zoom_in" else f"{ZOOM}*(1-t/{L})"
        chain = (f"scale=w='trunc({sw}*(1+{grow})/2)*2':h=-2:eval=frame:flags=bicubic,"
                 f"crop={sw}:{sh}")
    else:
        big = f"scale=w='trunc({sw}*(1+{ZOOM})/2)*2':h=-2:flags=bicubic,format=yuv444p"
        cx, cy = "(in_w-out_w)/2", "(in_h-out_h)/2"
        if motion == "pan_right":
            cx = f"(in_w-out_w)*t/{L}"
        elif motion == "pan_left":
            cx = f"(in_w-out_w)*(1-t/{L})"
        elif motion == "pan_down":
            cy = f"(in_h-out_h)*t/{L}"
        elif motion == "pan_up":
            cy = f"(in_h-out_h)*(1-t/{L})"
        chain = f"{big},crop={sw}:{sh}:x='{cx}':y='{cy}':exact=1"
    return f"{chain},scale={w}:{h}:flags=lanczos,setsar=1"


def _segment_clip(i: int, seg: Segment, length: float, size: tuple[int, int], workdir: Path, log: Path) -> Path:
    """소리 없는 영상 조각. length는 뒤 장면과 겹칠 전환 시간을 더한 길이."""
    w, h = size
    L = f"{length:.3f}"
    if seg.bg is not None:
        inputs = ["-loop", "1", "-framerate", str(FPS), "-t", L, "-i", str(seg.bg)]
        graph = f"[0:v]{_motion_chain(seg.motion, length, size)}[s0];"
    else:
        inputs = ["-f", "lavfi", "-t", L, "-i", f"color=c={BG_COLOR}:s={w}x{h}:r={FPS}"]
        graph = "[0:v]setsar=1[s0];"
    n = 0
    for png in seg.overlays:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", L, "-i", str(png)]
        graph += f"[s{n}][{n + 1}:v]overlay=0:0[s{n + 1}];"
        n += 1
    if seg.caption is not None:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", L, "-i", str(seg.caption)]
        rise = max(8, round(h * 0.03))
        graph += (f"[{n + 1}:v]format=rgba,fade=t=in:st=0:d={CAPTION_IN}:alpha=1[cap];"
                  f"[s{n}][cap]overlay=x=0:y='max(0,{rise}*(1-t/{CAPTION_IN}))'[s{n + 1}];")
        n += 1
    graph += f"[s{n}]format=yuv420p[v]"
    out = workdir / f"seg_{i:02d}.mp4"
    _run([*inputs, "-filter_complex", graph, "-map", "[v]", "-an", "-t", L, "-r", str(FPS),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)], log)
    return out


def _photo_for(post: BlogPost, image_index: int | None) -> str | None:
    if image_index is not None and 0 <= image_index < len(post.images):
        src = post.images[image_index].local_path
        if src and Path(src).exists():
            return src
    return None


def _build_segments(post: BlogPost, script: VideoScript, options: RenderOptions, size: tuple[int, int],
                    workdir: Path, on_step: Callable[[float], None] | None = None) -> list[Segment]:
    w, h = size
    big = (w * SUPERSAMPLE, h * SUPERSAMPLE)
    tpl = options.template
    title = (script.intro_title or script.yt_title or post.title).strip()
    photos = [_photo_for(post, s.image_index) for s in script.scenes]
    segs: list[Segment] = []

    def card(kind: str, photo: str | None, seconds: float, head: str, sub: str, tag: str) -> Segment:
        bg = None
        if photo:
            try:
                bg = visuals.blurred_bg(photo, workdir / f"{kind}_bg.jpg", big)
            except OSError:
                bg = None
        png = visuals.render_card_png(kind, head, sub, tag, w, h, workdir / f"{kind}.png", tpl)
        return Segment(duration=seconds, bg=bg, overlays=[png], motion="zoom_in")

    if options.intro and title:
        segs.append(card("intro", next((p for p in photos if p), None), INTRO_SEC, title, script.intro_sub, ""))
    for i, scene in enumerate(script.scenes):
        if not scene.audio_path or not scene.duration:
            raise UserError(FAILED)
        bg = None
        if photos[i]:
            try:
                bg = _prepare_bg(photos[i], workdir / f"bg_{i:02d}.jpg", size)
            except OSError:
                bg = None
        overlays = []
        if options.title_bar and (title or scene.place):
            overlays.append(visuals.render_overlay_png(title, scene.place, w, h, workdir / f"overlay_{i:02d}.png", tpl))
        caption = render_caption_png(scene.text, w, h, workdir / f"caption_{i:02d}.png", tpl)
        if scene.motion != "auto":       # 장면에서 직접 고른(또는 AI가 추천한) 움직임이 먼저
            motion = scene.motion
        else:
            motion = "zoom_in" if options.effects == "soft" else MOTIONS[i % len(MOTIONS)]
        segs.append(Segment(duration=scene.duration, bg=bg, overlays=overlays, caption=caption,
                            audio=scene.audio_path, motion=motion, transition=XFADE.get(scene.transition, "")))
        if on_step:   # 사진 준비도 오래 걸릴 수 있어 장면마다 알린다(진행률 표시, 멈추기 확인)
            on_step((i + 1) / len(script.scenes))
    if options.outro:
        segs.append(card("outro", next((p for p in reversed(photos) if p), None), OUTRO_SEC,
                         THANKS, SUBSCRIBE, options.channel))
    return segs


GROUP = 8   # 한 번에 잇는 토막 수. 장면이 많으면(긴 영상) 여러 묶음으로 나눠 잇고, 묶음끼리 다시 잇는다


@dataclass
class Part:
    """이어 붙일 한 덩어리. 영상은 뒤와 겹칠 전환 시간만큼 길게 들어 있다(맨 마지막 덩어리만 예외)."""
    video: Path
    duration: float          # 소리 기준 길이 (전환 겹침 제외)
    kind: str                # 이 덩어리로 들어올 때의 xfade 종류
    audio: str | None = None  # 따로 있는 소리 파일. 없고 muxed도 아니면 무음
    muxed: bool = False      # 영상 파일 안에 소리가 같이 들어 있음(묶음 결과)


def _merge(parts: list[Part], cross: float, out: Path, log: Path, tail: bool,
           final: tuple[Path | None, float] | None = None) -> None:
    """parts를 전환 효과로 이어 하나로 만든다. 전환은 다음 덩어리의 소리가 시작되는 순간부터 겹치므로
    소리 길이는 그대로다(전체 길이 = 길이의 합). tail이면 결과 영상 끝에 겹칠 시간이 남아 있다.
    final=(배경음악, 음량)이면 마지막 단계: 처음·끝 페이드와 배경음악을 넣고 MP4로 만든다."""
    n = len(parts)
    total = sum(p.duration for p in parts)
    args: list[str] = []
    for p in parts:
        args += ["-i", str(p.video)]
    a_src = []
    for i, p in enumerate(parts):
        if p.muxed:
            a_src.append(f"{i}:a")
        else:
            if p.audio:
                args += ["-i", p.audio]
            else:
                args += ["-f", "lavfi", "-t", f"{p.duration:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            a_src.append(f"{_count_inputs(args) - 1}:a")
    graph = "".join(f"[{i}:v]fps={FPS},settb=AVTB,setpts=PTS-STARTPTS[c{i}];" for i in range(n))
    last = "c0"
    offset = 0.0
    for i in range(1, n):
        offset += parts[i - 1].duration
        graph += f"[{last}][c{i}]xfade=transition={parts[i].kind}:duration={cross:.3f}:offset={offset:.3f}[x{i}];"
        last = f"x{i}"
    if final is not None:
        graph += (f"[{last}]fade=t=in:st=0:d={FADE},fade=t=out:st={max(0.0, total - FADE):.3f}:d={FADE},"
                  f"format=yuv420p[v];")
    else:
        graph += f"[{last}]format=yuv420p[v];"
    for i, p in enumerate(parts):
        graph += (f"[{a_src[i]}]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
                  f"atrim=0:{p.duration:.3f},asetpts=PTS-STARTPTS[a{i}];")
    graph += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[voice]"
    amap = "[voice]"
    if final is not None and final[0]:
        bgm, volume = final
        args += ["-stream_loop", "-1", "-i", str(bgm)]
        graph += (f";[{_count_inputs(args) - 1}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"volume={volume},atrim=0:{total:.3f},afade=t=out:st={max(0.0, total - 2):.3f}:d=2[b];"
                  f"[voice][b]amix=inputs=2:duration=first:normalize=0[a]")
        amap = "[a]"
    if final is not None:
        enc = ["-t", f"{total:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    else:   # 중간 결과: 화질 손실을 줄이고 소리는 무압축으로
        enc = ["-t", f"{total + (cross if tail else 0.0):.3f}", "-r", str(FPS), "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]
    # 한 번에 GROUP개까지만 넘기므로 명령 길이가 윈도우 제한(약 3만 자)을 넘지 않는다
    _run([*args, "-filter_complex", graph, "-map", "[v]", "-map", amap, *enc, str(out)], log)


def _count_inputs(args: list[str]) -> int:
    return sum(1 for a in args if a == "-i")


def _join(clips: list[Path], segs: list[Segment], cross: float, transitions: tuple[str, ...],
          bgm: Path | None, bgm_volume: float, out: Path, log: Path,
          on_progress: Callable[[float], None] | None = None) -> None:
    """조각들을 잇는다. 장면이 많아도(영상 길이 제한 없음) 한 번에 GROUP개씩만 FFmpeg에 넘긴다."""
    parts = [Part(video=c, duration=s.duration, audio=s.audio,
                  kind=s.transition or transitions[(i - 1) % len(transitions)])
             for i, (c, s) in enumerate(zip(clips, segs))]
    steps = 1
    k = len(parts)
    while k > GROUP:
        k = -(-k // GROUP)
        steps += k
    done = 0
    level = 0
    while len(parts) > GROUP:
        merged = []
        chunks = [parts[i:i + GROUP] for i in range(0, len(parts), GROUP)]
        for j, chunk in enumerate(chunks):
            is_last = j == len(chunks) - 1
            if len(chunk) == 1:
                merged.append(chunk[0])
            else:
                dest = out.parent / f"part_{level}_{j:03d}.mkv"
                _merge(chunk, cross, dest, log, tail=not is_last)
                merged.append(Part(video=dest, duration=sum(p.duration for p in chunk), kind=chunk[0].kind, muxed=True))
            done += 1
            if on_progress:
                on_progress(done / steps)
        parts = merged
        level += 1
    _merge(parts, cross, out, log, tail=False, final=(bgm, bgm_volume))


def render_video(post: BlogPost, script: VideoScript, options: RenderOptions, workdir: Path,
                 on_progress: Callable[[float], None] | None = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    log = workdir / "render.log"
    size = size_for(options.aspect, options.quality)
    report = on_progress or (lambda _: None)
    if not script.scenes:
        raise UserError(FAILED)
    segs = _build_segments(post, script, options, size, workdir, on_step=lambda p: report(round(0.1 * p, 3)))
    tpl = visuals.template(options.template)
    soft = options.effects == "soft"
    cross = min(SOFT_SEC if soft else tpl.transition_sec, min(s.duration for s in segs) / 2)
    transitions = visuals.SOFT if soft else tpl.transitions
    clips = []
    for i, seg in enumerate(segs):
        length = seg.duration + (cross if i < len(segs) - 1 else 0.0)
        clips.append(_segment_clip(i, seg, length, size, workdir, log))
        report(round(0.1 + 0.6 * (i + 1) / len(segs), 3))

    out = workdir / "output.mp4"
    _join(clips, segs, cross, transitions, resolve_bgm(options.bgm), options.bgm_volume, out, log,
          on_progress=lambda p: report(round(0.7 + 0.25 * p, 3)))
    report(0.97)
    try:  # 썸네일은 덤: 실패해도 영상은 살린다
        photo = next((p for s in script.scenes if (p := _photo_for(post, s.image_index))), None)
        visuals.render_thumbnail(photo, script.intro_title or script.yt_title or post.title, options.channel,
                                 workdir / "thumbnail.jpg", options.template)
    except Exception:
        with log.open("a", encoding="utf-8") as f:
            f.write("thumbnail failed\n")
    report(1.0)
    return out
