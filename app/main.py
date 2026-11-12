"""FastAPI 앱."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.errors import UserError
from app.jobs import JobManager, Steps
from app.models import JobState, RenderOptions, VideoScript
from app.paths import jobs_dir, music_dirs, resource_path, user_music_dir
from app.scraper.naver import validate_url

PORT = 8765
NO_KEY = "설정에서 API 키를 입력해 주세요."
PC_ONLY = "설정은 PC에서만 바꿀 수 있어요."
LOCAL_ONLY = "이 프로그램은 설치된 PC에서만 쓸 수 있어요."
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
UNKNOWN = "알 수 없는 오류가 났어요. 다시 시도해 주세요."
BAD_MUSIC = "MP3 파일만 올릴 수 있어요."
BAD_INPUT = "입력한 내용이 올바르지 않아요. 다시 확인해 주세요."

log = logging.getLogger(__name__)


def default_steps() -> Steps:
    from app.render import render_video
    from app.scraper.naver import download_images, fetch_post
    from app.script import generate_script
    from app.tts import synthesize_all

    return Steps(fetch_post=fetch_post, download_images=download_images, generate_script=generate_script,
                 synthesize_all=synthesize_all, render_video=render_video,
                 api_key=lambda: config.load_config().get("anthropic_api_key", ""))


class CreateJob(BaseModel):
    url: str
    options: RenderOptions = RenderOptions()


class UpdateScript(BaseModel):
    script: VideoScript
    options: RenderOptions


class Settings(BaseModel):
    anthropic_api_key: str


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _public(s: JobState) -> dict:
    """같은 와이파이의 기기에 보내는 상태. PC의 파일 경로(사용자 이름 포함)는 빼고 보낸다."""
    d = s.model_dump(exclude={"video_path"})
    d["has_video"] = bool(s.video_path)
    if d.get("post"):
        d["post"]["images"] = [{"url": i["url"]} for i in d["post"]["images"]]
    if d.get("script"):
        d["script"]["scenes"] = [{k: v for k, v in sc.items() if k != "audio_path"} for sc in d["script"]["scenes"]]
    return d


def _mask(key: str) -> str:
    return f"sk-ant-…{key[-4:]}" if key else ""


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title).strip()
    return " ".join(name.split())[:80] or "영상"


def create_app(manager: JobManager | None = None) -> FastAPI:
    manager = manager or JobManager(jobs_dir(), default_steps())
    app = FastAPI(title="블로그 영상 변환기", docs_url=None, redoc_url=None)
    app.state.manager = manager
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

    def pc_only(request: Request):
        if not _is_local(request):
            return JSONResponse({"message": PC_ONLY}, status_code=403)
        return None

    @app.get("/")
    def index():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/api/info")
    def info(request: Request):
        return {"has_api_key": bool(config.load_config().get("anthropic_api_key")),
                "is_local": _is_local(request)}

    @app.post("/api/jobs")
    def create_job(body: CreateJob):
        url = validate_url(body.url)
        if not manager.steps.api_key():
            raise UserError(NO_KEY)
        return _public(manager.create(url, body.options))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return _public(manager.get(job_id))

    @app.put("/api/jobs/{job_id}/script")
    def update_script(job_id: str, body: UpdateScript):
        return _public(manager.update_script(job_id, body.script, body.options))

    @app.post("/api/jobs/{job_id}/render")
    def render(job_id: str):
        return _public(manager.start_render(job_id))

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        return _public(manager.retry(job_id))

    @app.get("/api/jobs/{job_id}/image/{index}")
    def image(job_id: str, index: int):
        s = manager.get(job_id)
        if not s.post or not 0 <= index < len(s.post.images) or not s.post.images[index].local_path:
            return JSONResponse({"message": "사진이 없어요."}, status_code=404)
        return FileResponse(s.post.images[index].local_path, media_type="image/jpeg")

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

    @app.get("/api/music")
    def music():
        builtin, user = music_dirs()
        out = []
        for d, is_builtin in ((builtin, True), (user, False)):
            if d.exists():
                out += [{"name": p.name, "builtin": is_builtin} for p in sorted(d.glob("*.mp3"))]
        return out

    @app.post("/api/music")
    async def upload_music(request: Request, file: UploadFile = File(...)):
        if (r := pc_only(request)) is not None:
            return r
        name = _safe_filename(Path(file.filename or "").name)
        if not name.lower().endswith(".mp3"):
            raise UserError(BAD_MUSIC)
        (user_music_dir() / name).write_bytes(await file.read())
        return {"name": name, "builtin": False}

    @app.get("/api/settings")
    def get_settings(request: Request):
        if (r := pc_only(request)) is not None:
            return r
        return {"anthropic_api_key": _mask(config.load_config().get("anthropic_api_key", ""))}

    @app.put("/api/settings")
    def put_settings(request: Request, body: Settings):
        if (r := pc_only(request)) is not None:
            return r
        data = config.load_config()
        data["anthropic_api_key"] = body.anthropic_api_key.strip()
        config.save_config(data)
        return {"anthropic_api_key": _mask(data["anthropic_api_key"])}

    return app
