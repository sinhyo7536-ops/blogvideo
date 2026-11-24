"""FastAPI 앱."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field

from app import config
from app.errors import UserError
from app.jobs import NOT_CONFIRMED, JobManager, Steps, fingerprint
from app.models import JobState, RenderOptions, ScriptSettings, VideoScript
from app.paths import jobs_dir, music_dirs, resource_path, user_music_dir
from app.scraper.naver import validate_url

PORT = 8765
NO_KEY = "Claude API 키가 없어요. 설정에서 키를 넣어 주세요. (키 없이 쓰려면 \"AI 없이 직접 작성하기\")"
PC_ONLY = "설정은 PC에서만 바꿀 수 있어요."
NO_COMMAND = "어떻게 고칠지 버튼을 고르거나 직접 적어 주세요."
MAX_PHOTO_BYTES = 30 * 1024 * 1024
PHOTO_TOO_BIG = "사진이 너무 커요. 30MB보다 작은 사진을 올려 주세요."
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
VIDEO_TOO_BIG = "영상이 너무 커요. 4GB보다 작은 영상을 올려 주세요."
LOCAL_ONLY = "이 프로그램은 설치된 PC에서만 쓸 수 있어요."
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
UNKNOWN = "알 수 없는 오류가 났어요. 다시 시도해 주세요."
BAD_MUSIC = "MP3 파일만 올릴 수 있어요."
BAD_INPUT = "입력한 내용이 올바르지 않아요. 다시 확인해 주세요."

log = logging.getLogger(__name__)


class Presence:
    """화면(앱 창)이 열려 있는지: 화면이 몇 초마다 보내는 신호로 판단한다."""

    SILENCE = 150      # 신호가 이만큼 끊기면 창이 닫힌 것으로 본다(초). 최소화 시 브라우저가 타이머를 1분 간격으로 늦추므로 넉넉히
    AFTER_BYE = 8      # 창을 닫을 때 보내는 신호 뒤 이만큼 새 신호가 없으면 종료(새로고침과 구분)
    NEVER_OPENED = 180  # 창이 끝내 안 열리면 이만큼 뒤 종료

    def __init__(self, clock=None):
        import time

        self._clock = clock or time.monotonic
        self.started = self._clock()
        self.last_seen: float | None = None
        self.bye_at: float | None = None

    def seen(self) -> None:
        self.last_seen = self._clock()
        self.bye_at = None

    def bye(self) -> None:
        self.bye_at = self._clock()

    def should_exit(self, busy: bool) -> bool:
        if busy:
            return False
        now = self._clock()
        if self.last_seen is None:
            return now - self.started > self.NEVER_OPENED
        if self.bye_at is not None and now - self.bye_at > self.AFTER_BYE:
            return True
        return now - self.last_seen > self.SILENCE


def default_steps() -> Steps:
    from app.render import render_video, stop_current
    from app.scraper.naver import download_images, fetch_post
    from app.script import generate_script
    from app.ai.analyze import analyze
    from app.ai.photos import match as match_photos
    from app.ai.shorts import plan as plan_shorts
    from app.ai.youtube import generate as youtube_text
    from app.ai.rewrite import review, rewrite
    from app.director import direct
    from app.tts import preview, synthesize_all
    from app.ytmusic import fetch_audio

    return Steps(fetch_post=fetch_post, download_images=download_images, generate_script=generate_script,
                 synthesize_all=synthesize_all, render_video=render_video,
                 api_key=lambda: config.load_config().get("anthropic_api_key", ""),
                 voice_preview=preview, fetch_music=fetch_audio, rewrite=rewrite, review=review, match_photos=match_photos, plan_shorts=plan_shorts, youtube_text=youtube_text, abort=stop_current, direct=direct, analyze=analyze,
                 write_script=lambda post, key, analysis, settings, voice: generate_script(
                     post, key, analysis=analysis, settings=settings, voice=voice),
                 channel_name=lambda: config.load_config().get("channel_name", ""))


class CreateJob(BaseModel):
    url: str
    urls: list[str] = Field(default_factory=list, max_length=10)   # 함께 합칠 다른 글 주소
    options: RenderOptions = RenderOptions()
    mode: Literal["api", "load", "manual", "text"] = "api"   # load: 글·사진만, text: 블로그 글 그대로, manual: 빈 대본
    settings: ScriptSettings = ScriptSettings()


class WriteMode(BaseModel):
    mode: Literal["api", "manual", "text"]
    settings: ScriptSettings | None = None
    reanalyze: bool = False


class VoicePreview(BaseModel):
    voice: str = "female"
    speed: Literal["slow", "normal", "fast"] = "normal"


class YoutubeMusic(BaseModel):
    url: str


class UpdateScript(BaseModel):
    script: VideoScript
    options: RenderOptions
    version_label: str = Field(default="", max_length=40)   # 있으면 바꾸기 전 대본을 버전으로 남김


class RewriteRequest(BaseModel):
    indexes: list[int] = Field(min_length=1, max_length=300)
    preset: str | None = None
    command: str = Field(default="", max_length=200)


class ThumbText(BaseModel):
    text: str = Field(min_length=1, max_length=40)


class ShortsBody(BaseModel):
    script: VideoScript


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class VersionLabel(BaseModel):
    label: str = Field(default="직접 저장", max_length=40)


class Settings(BaseModel):
    anthropic_api_key: str | None = None
    models: dict[str, str] | None = None   # {작업: 모델 ID}
    channel_name: str | None = None


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _public(s: JobState) -> dict:
    """같은 와이파이의 기기에 보내는 상태. PC의 파일 경로(사용자 이름 포함)는 빼고 보낸다."""
    d = s.model_dump(exclude={"video_path", "shorts_path"})
    d["has_video"] = bool(s.video_path)
    d["has_shorts"] = bool(s.shorts_path) and Path(s.shorts_path).exists()
    if d.get("shorts_script"):
        d["shorts_script"]["scenes"] = [{k: v for k, v in sc.items() if k != "audio_path"}
                                        for sc in d["shorts_script"]["scenes"]]
    d["is_confirmed"] = bool(s.script) and s.confirmed == fingerprint(s.script)
    d["has_thumbnail"] = bool(_thumbnail_path(s))
    if d.get("post"):
        d["post"]["images"] = [{"url": i["url"]} for i in d["post"]["images"]]
        d["post"]["videos"] = [{k: v for k, v in x.items() if k != "local_path"} for x in d["post"].get("videos", [])]
    if d.get("script"):
        d["script"]["scenes"] = [{k: v for k, v in sc.items() if k != "audio_path"} for sc in d["script"]["scenes"]]
    return d


def _thumbnail_path(s: JobState) -> Path | None:
    if not s.video_path:
        return None
    p = Path(s.video_path).parent / "thumbnail.jpg"
    return p if p.exists() else None


def _fingerprint(path: Path) -> str:
    import hashlib

    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:6]}…{key[-4:]}"


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title).strip()
    return " ".join(name.split())[:80] or "영상"


def create_app(manager: JobManager | None = None) -> FastAPI:
    manager = manager or JobManager(jobs_dir(), default_steps())
    app = FastAPI(title="블로그 영상 변환기", docs_url=None, redoc_url=None)
    app.state.manager = manager
    app.state.presence = Presence()
    web = resource_path("app/web")

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        """이 PC에서, 이 프로그램 화면에서 온 요청만 받는다.

        - 다른 기기(같은 와이파이 포함) 차단
        - 다른 웹사이트가 주소를 바꿔치기(DNS 리바인딩)하거나 몰래 보내는 요청(CSRF) 차단
        """
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        origin = request.headers.get("origin")
        bad_origin = False
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            o = origin.split("://", 1)[-1].rsplit(":", 1)[0].lower()
            bad_origin = o not in _LOCAL_HOSTS
        if not _is_local(request) or host not in _LOCAL_HOSTS or bad_origin:
            return JSONResponse({"message": LOCAL_ONLY}, status_code=403)
        return await call_next(request)

    @app.exception_handler(UserError)
    async def _user_error(request: Request, e: UserError):
        return JSONResponse({"message": e.message}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _bad_input(request: Request, e: RequestValidationError):
        return JSONResponse({"message": BAD_INPUT}, status_code=400)

    @app.exception_handler(KeyError)
    async def _not_found(request: Request, e: KeyError):
        return JSONResponse({"message": "작업을 찾을 수 없어요."}, status_code=404)

    @app.exception_handler(Exception)
    async def _unknown(request: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse({"message": UNKNOWN}, status_code=500)

    def need_key(mode: str) -> None:
        if mode == "api" and not manager.steps.api_key():
            raise UserError(NO_KEY)

    def pc_only(request: Request):
        if not _is_local(request):
            return JSONResponse({"message": PC_ONLY}, status_code=403)
        return None

    @app.get("/")
    def index():
        # 화면 파일 주소에 내용 지문을 붙인다: 앱을 새로 설치하면 주소가 바뀌어 창이 예전 화면 코드를 쓰지 않는다
        # (2026-11-15: 캐시에 남은 0.6.0 화면이 지운 '제미나이' 모드를 보내 "입력한 내용이 올바르지 않아요"가 났다)
        html = (web / "index.html").read_text("utf-8")
        for name in ("app.js", "app.css"):
            html = html.replace(f"/static/{name}\"", f"/static/{name}?v={_fingerprint(web / name)}\"")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def _revalidate_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"   # 쓸 때마다 바뀌었는지 확인 (이 PC 안이라 빠름)
        return response

    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.post("/api/heartbeat", status_code=204)
    def heartbeat():
        app.state.presence.seen()

    @app.post("/api/bye", status_code=204)
    def bye():
        app.state.presence.bye()

    @app.get("/api/info")
    def info(request: Request):
        cfg = config.load_config()
        return {"has_api_key": bool(cfg.get("anthropic_api_key")),
                "is_local": _is_local(request)}

    @app.post("/api/jobs")
    def create_job(body: CreateJob):
        url = validate_url(body.url)
        more = [validate_url(u) for u in body.urls if u.strip()]
        need_key(body.mode)
        return _public(manager.create(url, body.options, body.mode, body.settings, urls=more))

    @app.get("/api/jobs")
    def list_jobs():
        """프로젝트 목록 (최근 것이 먼저)."""
        from app.projects import summary

        return {"jobs": [summary(s) for s in manager.list_jobs()]}

    @app.patch("/api/jobs/{job_id}")
    def rename_job(job_id: str, body: Rename):
        return _public(manager.rename(job_id, body.name))

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str):
        manager.delete(job_id)

    @app.post("/api/jobs/{job_id}/sources/retry")
    def retry_sources(job_id: str):
        """불러오지 못한 글 다시 불러오기."""
        return _public(manager.retry_sources(job_id))

    @app.post("/api/jobs/{job_id}/photos")
    async def upload_photo(job_id: str, file: UploadFile = File(...)):
        """사진 직접 올리기."""
        data = await file.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise UserError(PHOTO_TOO_BIG)
        return _public(manager.add_photo(job_id, data, file.filename or ""))

    @app.post("/api/jobs/{job_id}/videos")
    async def upload_video(job_id: str, file: UploadFile = File(...)):
        """영상 직접 올리기: 큰 파일도 메모리에 다 올리지 않고 조금씩 임시 파일에 쓴다."""
        manager.get(job_id)
        tmp_dir = manager.dir(job_id) / "videos"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"upload_{os.getpid()}_{id(file)}.part"
        size = 0
        try:
            with tmp.open("wb") as f:
                while chunk := await file.read(4 * 1024 * 1024):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise UserError(VIDEO_TOO_BIG)
                    f.write(chunk)
            return _public(await asyncio.to_thread(manager.add_video, job_id, tmp, file.filename or ""))
        finally:
            tmp.unlink(missing_ok=True)

    def _video_ref(job_id: str, index: int):
        s = manager.get(job_id)
        videos = s.post.videos if s.post else []
        if not 0 <= index < len(videos) or not Path(videos[index].local_path).exists():
            return None
        return videos[index]

    @app.get("/api/jobs/{job_id}/videos/{index}")
    def video_file(job_id: str, index: int):
        ref = _video_ref(job_id, index)
        if ref is None:
            return JSONResponse({"message": "영상이 없어요."}, status_code=404)
        import mimetypes

        kind = mimetypes.guess_type(ref.local_path)[0] or "video/mp4"
        return FileResponse(ref.local_path, media_type=kind)

    @app.get("/api/jobs/{job_id}/videos/{index}/thumb")
    def video_thumb(job_id: str, index: int):
        ref = _video_ref(job_id, index)
        thumb = Path(ref.local_path).with_name(f"thumb_{index:02d}.jpg") if ref else None
        if thumb is None or not thumb.exists():
            return JSONResponse({"message": "영상이 없어요."}, status_code=404)
        return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/jobs/{job_id}/write")
    def write(job_id: str, body: WriteMode):
        """불러온 글로 대본 만들기: Claude(api) 또는 직접 작성(manual)."""
        need_key(body.mode)
        return _public(manager.write(job_id, body.mode, body.settings, body.reanalyze))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return _public(manager.get(job_id))

    @app.put("/api/jobs/{job_id}/script")
    def update_script(job_id: str, body: UpdateScript):
        return _public(manager.update_script(job_id, body.script, body.options, body.version_label))

    @app.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str):
        """대본 확정: 사용자가 확인한 대본만 영상으로 만든다."""
        return _public(manager.confirm(job_id))

    @app.post("/api/jobs/{job_id}/render")
    def render(job_id: str):
        s = manager.get(job_id)
        if s.script is not None and not manager.is_confirmed(job_id):
            raise UserError(NOT_CONFIRMED)
        return _public(manager.start_render(job_id))

    @app.post("/api/jobs/{job_id}/ai-rewrite")
    def ai_rewrite(job_id: str, body: RewriteRequest):
        """고른 장면만 AI가 다시 쓴 제안 (적용은 화면에서)."""
        from app.ai.rewrite import command_text

        command = command_text(body.preset, body.command)
        if not command:
            raise UserError(NO_COMMAND)
        return {"suggestions": manager.suggest_rewrite(job_id, body.indexes, command)}

    @app.post("/api/jobs/{job_id}/match-photos")
    def match_photos(job_id: str):
        """장면마다 글과 사진이 맞는 정도와 추천 사진 (적용은 화면에서)."""
        return {"matches": manager.suggest_photos(job_id)}

    @app.post("/api/jobs/{job_id}/ai-review")
    def ai_review(job_id: str):
        """맞춤법·문맥 검토 제안."""
        return {"fixes": manager.suggest_review(job_id)}

    @app.get("/api/jobs/{job_id}/versions")
    def versions(job_id: str):
        return {"versions": manager.versions(job_id)}

    @app.post("/api/jobs/{job_id}/versions")
    def save_version(job_id: str, body: VersionLabel):
        return {"n": manager.save_version(job_id, body.label)}

    @app.post("/api/jobs/{job_id}/versions/{n}/restore")
    def restore_version(job_id: str, n: int):
        return _public(manager.restore_version(job_id, n))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        """진행 중인 작업 멈추기."""
        return _public(manager.cancel(job_id))

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        return _public(manager.retry(job_id))

    @app.get("/api/jobs/{job_id}/image/{index}")
    def image(job_id: str, index: int, small: int = 0):
        s = manager.get(job_id)
        if not s.post or not 0 <= index < len(s.post.images) or not s.post.images[index].local_path:
            return JSONResponse({"message": "사진이 없어요."}, status_code=404)
        path = Path(s.post.images[index].local_path)
        if small:  # 화면용 작은 사진 (원본은 최대 3840px라 목록에 그대로 쓰면 무겁다)
            thumb = path.with_name(f"small_{index:02d}.jpg")
            if not thumb.exists():
                try:
                    from PIL import Image, ImageOps

                    with Image.open(path) as im:
                        img = ImageOps.exif_transpose(im).convert("RGB")
                        img.thumbnail((480, 480))
                        img.save(thumb, quality=80)
                except OSError:
                    thumb = path
            path = thumb
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/jobs/{job_id}/video")
    def video(job_id: str, download: int = 0):
        s = manager.get(job_id)
        if not s.video_path or not Path(s.video_path).exists():
            return JSONResponse({"message": "아직 영상이 없어요."}, status_code=404)
        headers = {"Cache-Control": "no-cache"}
        if download:
            title = _safe_filename(s.script.yt_title if s.script else "영상")
            headers["Content-Disposition"] = f"attachment; filename=\"video.mp4\"; filename*=UTF-8''{quote(title + '.mp4')}"
        return FileResponse(s.video_path, media_type="video/mp4", headers=headers)

    @app.post("/api/jobs/{job_id}/thumbnail")
    def remake_thumbnail(job_id: str, body: ThumbText):
        """썸네일 문구를 바꿔 다시 만들기."""
        return _public(manager.remake_thumbnail(job_id, body.text))

    @app.post("/api/jobs/{job_id}/youtube")
    def youtube_text(job_id: str):
        """AI가 쓰는 유튜브 제목 후보·설명·해시태그·썸네일 문구·키워드."""
        return manager.make_youtube(job_id)

    @app.post("/api/jobs/{job_id}/shorts/plan")
    def shorts_plan(job_id: str):
        """쇼츠 대본 제안 (확인·수정한 뒤 만든다)."""
        return {"script": manager.plan_shorts(job_id).model_dump(exclude={"scenes": {"__all__": {"audio_path"}}})}

    @app.post("/api/jobs/{job_id}/shorts")
    def shorts_render(job_id: str, body: ShortsBody):
        return _public(manager.start_shorts(job_id, body.script))

    @app.get("/api/jobs/{job_id}/shorts/video")
    def shorts_video(job_id: str, download: int = 0):
        s = manager.get(job_id)
        if not s.shorts_path or not Path(s.shorts_path).exists():
            return JSONResponse({"message": "아직 쇼츠가 없어요."}, status_code=404)
        headers = {"Cache-Control": "no-cache"}
        if download:
            title = _safe_filename((s.script.yt_title if s.script else "영상") + "_쇼츠")
            headers["Content-Disposition"] = f"attachment; filename=\"shorts.mp4\"; filename*=UTF-8''{quote(title + '.mp4')}"
        return FileResponse(s.shorts_path, media_type="video/mp4", headers=headers)

    @app.post("/api/jobs/{job_id}/checkup")
    def checkup(job_id: str):
        """영상 검수: 사진 반복·빠진 사진·자막 길이와 속도·장면 시간·글과 사진 점수 + AI 오탈자·문맥."""
        return manager.checkup(job_id)

    @app.get("/api/jobs/{job_id}/thumbnail")
    def thumbnail(job_id: str, download: int = 0):
        s = manager.get(job_id)
        p = _thumbnail_path(s)
        if p is None:
            return JSONResponse({"message": "아직 썸네일이 없어요."}, status_code=404)
        headers = {"Cache-Control": "no-cache"}
        if download:
            title = _safe_filename(s.script.yt_title if s.script else "썸네일")
            name = quote(title + "_썸네일.jpg")
            headers["Content-Disposition"] = f"attachment; filename=\"thumbnail.jpg\"; filename*=UTF-8''{name}"
        return FileResponse(p, media_type="image/jpeg", headers=headers)

    @app.get("/api/music")
    def music():
        builtin, user = music_dirs()
        out = []
        for d, is_builtin in ((builtin, True), (user, False)):
            if d.exists():
                out += [{"name": p.name, "builtin": is_builtin} for p in sorted(d.glob("*.mp3"))]
        return out

    @app.get("/api/music/file/{name}")
    def music_file(name: str):
        """배경음악 미리 듣기."""
        from app.render import resolve_bgm

        p = resolve_bgm(name)
        if p is None:
            return JSONResponse({"message": "음악 파일이 없어요."}, status_code=404)
        return FileResponse(p, media_type="audio/mpeg")

    @app.post("/api/music/youtube")
    def youtube_music(body: YoutubeMusic):
        """유튜브 링크의 소리를 내 음악 폴더에 MP3로 가져온다."""
        from app.ytmusic import normalize

        url = normalize(body.url)
        if manager.steps.fetch_music is None:
            raise UserError(UNKNOWN)
        return {"name": manager.steps.fetch_music(url, user_music_dir()), "builtin": False}

    @app.get("/api/presets")
    def presets():
        """영상 프리셋 (스타일·전환·제목 띠 등을 한 번에)."""
        from app.visuals import PRESETS

        return {"presets": [{"key": k, "label": v["label"], "help": v["help"], "options": v["options"]}
                            for k, v in PRESETS.items()]}

    @app.get("/api/voices")
    def voices():
        from app.tts import VOICE_LIST

        return [{"key": k, "label": v.label, "group": v.group} for k, v in VOICE_LIST.items()]

    @app.post("/api/voice-preview")
    def voice_preview(body: VoicePreview):
        """목소리 미리 듣기 (한 번 만든 것은 다시 쓴다)."""
        from app.models import VOICE_KEYS
        from app.paths import config_dir

        if body.voice not in VOICE_KEYS or manager.steps.voice_preview is None:
            raise UserError(BAD_INPUT)
        out = config_dir() / "previews" / f"{body.voice}_{body.speed}.mp3"
        if not out.exists() or out.stat().st_size == 0:
            manager.steps.voice_preview(body.voice, body.speed, out)
        return FileResponse(out, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})

    @app.post("/api/music")
    async def upload_music(request: Request, file: UploadFile = File(...)):
        if (r := pc_only(request)) is not None:
            return r
        name = _safe_filename(Path(file.filename or "").name)
        if not name.lower().endswith(".mp3"):
            raise UserError(BAD_MUSIC)
        (user_music_dir() / name).write_bytes(await file.read())
        return {"name": name, "builtin": False}

    @app.post("/api/keyed-installer")
    async def keyed_installer(request: Request, file: UploadFile = File(...)):
        """설치 파일에 지금 저장된 API 키를 덧붙여 '지인용 설치 파일'을 만든다 (이 PC 안에서만)."""
        if (r := pc_only(request)) is not None:
            return r
        from app.keybundle import embed

        cfg = config.load_config()
        keys = {k: cfg.get(k, "") for k in config.SECRET_KEYS if cfg.get(k)}
        if not keys:
            raise UserError("먼저 위에서 API 키를 저장해 주세요.")
        data = await file.read()
        if len(data) > 400 * 1024 * 1024:
            raise UserError("파일이 너무 커요. 블로그 영상 변환기 설치 파일을 골라 주세요.")
        out = embed(data, keys)
        name = quote("블로그영상변환기_설치_키포함.exe")
        return Response(out, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=\"BlogVideo-Setup-with-key.exe\"; filename*=UTF-8''{name}"})

    @app.get("/api/settings")
    def get_settings(request: Request):
        if (r := pc_only(request)) is not None:
            return r
        from app.ai import models as ai_models

        cfg = config.load_config()
        return {**{k: _mask(cfg.get(k, "")) for k in config.SECRET_KEYS}, "channel_name": cfg.get("channel_name", ""),
                "models": ai_models.catalog()}

    @app.put("/api/settings")
    def put_settings(request: Request, body: Settings):
        if (r := pc_only(request)) is not None:
            return r
        data = config.load_config()
        for k in config.SECRET_KEYS:
            v = getattr(body, k)
            if v and v.strip():
                data[k] = v.strip()
        if body.channel_name is not None:  # 빈 칸으로 저장하면 지움(블로그 이름을 대신 씀)
            data["channel_name"] = " ".join(body.channel_name.split())[:40]
        config.save_config(data)
        from app.ai import models as ai_models

        if body.models is not None:
            ai_models.save(body.models)
        return {**{k: _mask(data.get(k, "")) for k in config.SECRET_KEYS}, "channel_name": data.get("channel_name", ""),
                "models": ai_models.catalog()}

    @app.post("/api/settings/check-models")
    def check_models(request: Request):
        """설정된 Claude 모델마다 짧게 물어 연결을 확인한다 (아주 적은 비용이 든다)."""
        if (r := pc_only(request)) is not None:
            return r
        from app.ai import models as ai_models

        return ai_models.check(manager.steps.api_key())

    return app
