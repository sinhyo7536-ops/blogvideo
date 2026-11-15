"""작업(job) 관리: 큐, 상태 저장, 단계 실행."""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.errors import UserError
from app.models import BlogPost, JobState, RenderOptions, VideoScript

log = logging.getLogger(__name__)

UNKNOWN = "알 수 없는 오류가 났어요. 다시 시도해 주세요."
NO_POSTS = "글을 하나도 불러오지 못했어요. 주소를 확인하고 다시 시도해 주세요."
BAD_PHOTO = "사진 파일을 열 수 없어요. JPG나 PNG 사진을 올려 주세요."
SAME_PHOTO = "이미 들어 있는 사진이에요."
NOTHING_TO_RETRY = "다시 불러올 글이 없어요."
NO_VIDEO = "먼저 영상을 만들어 주세요."
NO_SHORTS = "쇼츠 대본이 비어 있어요."
SHORTS_FAILED = "쇼츠를 만들지 못했어요. 다시 시도해 주세요."
INTERRUPTED = "작업이 중간에 멈췄어요. 다시 시도해 주세요."
BUSY = "지금은 바꿀 수 없어요. 진행 중인 작업이 끝난 뒤 다시 해 주세요."
NO_SCRIPT = "대본이 아직 없어요."
NO_POST = "먼저 블로그 글을 불러와 주세요."
STOPPED = "불러오기를 멈췄어요."
NOT_CONFIRMED = "영상을 만들기 전에 대본을 확인하고 '대본 확정'을 눌러 주세요."
NO_KEY_AI = "AI 도구를 쓰려면 설정에서 Claude API 키를 넣어 주세요."
NO_VERSION = "그 버전을 찾을 수 없어요."
MAX_VERSIONS = 40


class Cancelled(Exception):
    """사용자가 \"멈추기\"를 눌렀다."""

_ACTIVE = {"queued", "scraping", "analyzing", "scripting", "directing", "tts", "rendering"}
NO_AI_KEY = "연출 요청을 반영하려면 Claude API 키가 필요해요. 설정에서 키를 넣거나, 연출 요청 칸을 비워 주세요."


@dataclass
class Steps:
    fetch_post: Callable
    download_images: Callable
    generate_script: Callable
    synthesize_all: Callable
    render_video: Callable
    api_key: Callable[[], str]
    channel_name: Callable[[], str] = lambda: ""   # 설정의 채널 이름 (비면 블로그 이름)
    analyze: Callable | None = None         # (글, 키) → 여행 분석 (app.ai.analyze)
    write_script: Callable | None = None    # (글, 키, 분석, 설정, 목소리) → 대본. 없으면 generate_script(글, 키)
    direct: Callable | None = None          # (대본, 설정, 키) → (대본, 설정): 연출 요청 반영
    abort: Callable | None = None           # 돌고 있는 외부 작업(FFmpeg)을 바로 끝낸다
    voice_preview: Callable | None = None   # (목소리, 속도, 저장할 파일) → 파일
    fetch_music: Callable | None = None     # (유튜브 주소, 음악 폴더) → 파일 이름
    rewrite: Callable | None = None         # (대본, 장면 번호들, 명령, 키, 글, 분석) → 제안 목록 (app.ai.rewrite)
    review: Callable | None = None          # (대본, 키) → 맞춤법·문맥 제안 목록
    match_photos: Callable | None = None    # (대본, 글, 분석, 키) → 장면별 사진 추천 (app.ai.photos)
    plan_shorts: Callable | None = None     # (대본, 글, 분석, 키) → 쇼츠 대본 (app.ai.shorts)
    youtube_text: Callable | None = None    # (대본, 글, 분석, 영상 설정, 키) → 유튜브 글 (app.ai.youtube)


def fingerprint(script: VideoScript | None) -> str:
    """사용자가 고칠 수 있는 내용의 지문. 목소리 파일·길이처럼 영상을 만들며 채우는 값은 뺀다."""
    if script is None:
        return ""
    d = script.model_dump(exclude={"direction_done": True,
                                   "scenes": {"__all__": {"duration", "audio_path", "direction_done",
                                                          "photo_score", "photo_alts"}}})
    return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def blank_script(post: BlogPost) -> VideoScript:
    """직접 작성용 빈 대본: 장면은 비우고 유튜브 정보만 글에서 채워 둔다."""
    from app.autoscript import _tags
    from app.script import short_title

    title = post.title.strip()
    return VideoScript(scenes=[], yt_title=title[:60], yt_description=f"원문: {post.url}", yt_tags=_tags(title),
                       intro_title=short_title(title) if title else "")


class JobManager:
    def __init__(self, root: Path, steps: Steps):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.steps = steps
        self._lock = threading.RLock()
        self._jobs: dict[str, JobState] = {}
        self._queue: queue.Queue = queue.Queue()
        self._cancel: set[str] = set()
        self._return: dict[str, str] = {}   # 글 다시 불러오기가 끝나면 돌아갈 단계
        self._load()
        self._worker = threading.Thread(target=self._run, daemon=True, name="job-worker")
        self._worker.start()

    # ---------- 상태 ----------
    def _load(self) -> None:
        for f in self.root.glob("*/state.json"):
            try:
                s = JobState.model_validate_json(f.read_text("utf-8"))
            except ValueError:
                continue
            if s.stage in _ACTIVE:
                if s.stage == "scraping" and s.script is not None:
                    # 실패한 글 다시 불러오기 중에 꺼졌다: 대본은 그대로 두고 편집 화면으로
                    self._jobs[s.id] = s = s.model_copy(update={"stage": "ready", "progress": 1.0})
                    self._save(s)
                    continue
                if s.stage in ("directing", "tts", "rendering") or (s.stage == "queued" and s.script):
                    phase = "render"
                else:
                    phase = "script" if s.post else "prepare"
                s = s.model_copy(update={"stage": "error", "message": INTERRUPTED, "failed_phase": phase})
            self._jobs[s.id] = s
            self._save(s)

    def _save(self, s: JobState) -> None:
        d = self.root / s.id
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "state.tmp"
        tmp.write_text(s.model_dump_json(), "utf-8")
        for attempt in range(5):  # 윈도우에서 백신/색인이 파일을 잠깐 잡고 있을 수 있음
            try:
                tmp.replace(d / "state.json")
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def _set(self, job_id: str, **changes) -> JobState:
        with self._lock:
            s = self._jobs[job_id].model_copy(update=changes)
            self._jobs[job_id] = s
            try:
                self._save(s)
            except OSError:
                log.exception("could not save state for %s", job_id)  # 메모리 상태는 유지
            return s

    def is_busy(self) -> bool:
        """진행 중이거나 기다리는 작업이 있으면 True."""
        with self._lock:
            return any(s.stage in _ACTIVE for s in self._jobs.values())

    def get(self, job_id: str) -> JobState:
        with self._lock:
            return self._jobs[job_id]

    def dir(self, job_id: str) -> Path:
        return self.root / job_id

    # ---------- 공개 동작 ----------
    def create(self, url: str, options: RenderOptions, mode: str = "api", settings=None,
               urls: list[str] | None = None) -> JobState:
        all_urls = []
        for u in [url, *(urls or [])]:      # 똑같은 주소는 한 번만
            if u and u not in all_urls:
                all_urls.append(u)
        s = JobState(id=uuid.uuid4().hex[:12], url=all_urls[0], options=options, mode=mode,
                     urls=all_urls if len(all_urls) > 1 else [])
        if settings is not None:
            s = s.model_copy(update={"settings": settings})
        with self._lock:
            self._jobs[s.id] = s
            self._save(s)
        self._queue.put((s.id, "prepare"))
        return s

    def update_script(self, job_id: str, script: VideoScript, options: RenderOptions,
                      version_label: str = "") -> JobState:
        """대본 저장(자동 저장 포함). version_label 이 있으면 바꾸기 전 대본을 버전 기록에 남긴다."""
        s = self.get(job_id)
        if s.stage in _ACTIVE or s.post is None:
            raise UserError(BUSY)
        if version_label and s.script is not None and fingerprint(s.script) != fingerprint(script):
            self._snapshot(job_id, s.script, version_label)
        return self._set(job_id, script=script, options=options, stage="ready", message="", progress=1.0)

    # ---------- 대본 확정 ----------
    def confirm(self, job_id: str) -> JobState:
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        if s.script is None or not any(x.text.strip() for x in s.script.scenes):
            raise UserError(NO_SCRIPT)
        fp = fingerprint(s.script)
        if s.confirmed != fp:
            self._snapshot(job_id, s.script, "대본 확정")
        return self._set(job_id, confirmed=fp)

    def is_confirmed(self, job_id: str) -> bool:
        s = self.get(job_id)
        return bool(s.script) and s.confirmed == fingerprint(s.script)

    # ---------- 버전 기록 ----------
    def _versions_dir(self, job_id: str) -> Path:
        return self.dir(job_id) / "versions"

    def _snapshot(self, job_id: str, script: VideoScript, label: str) -> int:
        d = self._versions_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        files = sorted(d.glob("*.json"))
        n = int(files[-1].stem) + 1 if files else 1
        data = {"n": n, "label": label[:40], "time": time.time(), "script": script.model_dump(mode="json")}
        (d / f"{n:04d}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        for old in files[:max(0, len(files) + 1 - MAX_VERSIONS)]:
            old.unlink(missing_ok=True)
        return n

    def save_version(self, job_id: str, label: str = "직접 저장") -> int:
        s = self.get(job_id)
        if s.script is None:
            raise UserError(NO_SCRIPT)
        return self._snapshot(job_id, s.script, label or "직접 저장")

    def versions(self, job_id: str) -> list[dict]:
        self.get(job_id)
        out = []
        for f in sorted(self._versions_dir(job_id).glob("*.json"), reverse=True):
            try:
                d = json.loads(f.read_text("utf-8"))
                sc = VideoScript.model_validate(d["script"])
            except (ValueError, KeyError):
                continue
            out.append({"n": d["n"], "label": d.get("label", ""), "time": d.get("time", 0),
                        "scenes": len(sc.scenes), "chars": sum(len(x.text) for x in sc.scenes),
                        "first": sc.scenes[0].text[:40] if sc.scenes else ""})
        return out

    def restore_version(self, job_id: str, n: int) -> JobState:
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        f = self._versions_dir(job_id) / f"{int(n):04d}.json"
        if not f.exists():
            raise UserError(NO_VERSION)
        script = VideoScript.model_validate(json.loads(f.read_text("utf-8"))["script"])
        if s.script is not None and fingerprint(s.script) != fingerprint(script):
            self._snapshot(job_id, s.script, "되돌리기 전")
        return self._set(job_id, script=script, stage="ready", message="", progress=1.0)

    # ---------- AI 편집 도구 (제안만 돌려준다) ----------
    def _ai_ready(self, job_id: str) -> tuple[JobState, str]:
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        if s.script is None:
            raise UserError(NO_SCRIPT)
        key = self.steps.api_key()
        if not key:
            raise UserError(NO_KEY_AI)
        return s, key

    def suggest_rewrite(self, job_id: str, indexes: list[int], command: str) -> list[dict]:
        s, key = self._ai_ready(job_id)
        return self.steps.rewrite(s.script, indexes, command, key, s.post, s.analysis)

    def suggest_photos(self, job_id: str) -> list[dict]:
        """장면별 사진 추천. 여행 분석(사진 설명)이 없으면 먼저 분석한다."""
        s, key = self._ai_ready(job_id)
        if not s.post or not s.post.images:
            return []
        analysis = s.analysis
        if analysis is None and self.steps.analyze is not None:
            a = self.steps.analyze(s.post, key)
            analysis = a.model_dump() if hasattr(a, "model_dump") else a
            self._set(job_id, analysis=analysis)
        return self.steps.match_photos(s.script, s.post, analysis, key)

    def suggest_review(self, job_id: str) -> list[dict]:
        s, key = self._ai_ready(job_id)
        return self.steps.review(s.script, key)

    def start_render(self, job_id: str) -> JobState:
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        if s.script is None or s.post is None:
            raise UserError(NO_SCRIPT)
        s = self._set(job_id, stage="queued", progress=0.0, message="", failed_phase=None)
        self._queue.put((job_id, "render"))
        return s

    def retry(self, job_id: str) -> JobState:
        s = self.get(job_id)
        if s.stage != "error":
            raise UserError(BUSY)
        phase = s.failed_phase or "prepare"
        s = self._set(job_id, stage="queued", progress=0.0, message="", failed_phase=None)
        self._queue.put((job_id, phase))
        return s

    def cancel(self, job_id: str) -> JobState:
        """진행 중인 작업을 멈춘다. 영상 만들기는 대본 고치기로, 대본 쓰기는 불러온 글 화면으로 돌아간다.
        (이어 하기는 없다: 다시 누르면 처음부터 만든다.)"""
        s = self.get(job_id)
        if s.stage not in _ACTIVE:
            return s
        with self._lock:
            self._cancel.add(job_id)
        if self.steps.abort and s.stage in ("directing", "tts", "rendering"):
            try:
                self.steps.abort()
            except Exception:
                log.exception("abort failed")
        return self.get(job_id)

    def _check(self, job_id: str) -> None:
        if job_id in self._cancel:
            raise Cancelled()

    def _tick(self, job_id: str, progress: float) -> None:
        self._check(job_id)
        self._set(job_id, progress=progress)

    def _stopped(self, job_id: str, phase: str) -> None:
        with self._lock:
            self._cancel.discard(job_id)
        s = self.get(job_id)
        if phase in ("sources", "shorts"):
            self._set(job_id, stage=self._return.pop(job_id, "ready"), progress=1.0, message="")
        elif phase == "render":
            self._set(job_id, stage="ready", progress=1.0, message="", failed_phase=None)
        elif s.post is not None:
            self._set(job_id, stage="loaded", progress=1.0, message="", failed_phase=None)
        else:
            self._set(job_id, stage="error", message=STOPPED, failed_phase="prepare")

    def write(self, job_id: str, mode: str, settings=None, reanalyze: bool = False) -> JobState:
        """불러온 글로 대본 만들기. manual이면 빈 대본으로 바로 편집 화면으로, 나머지는 AI/자동 작성."""
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        if s.post is None:
            raise UserError(NO_POST)
        if settings is not None:
            self._set(job_id, settings=settings)
        if reanalyze:
            self._set(job_id, analysis=None)
        if mode == "manual":
            return self._set(job_id, mode="manual", script=blank_script(s.post), stage="ready", progress=1.0,
                             message="", failed_phase=None)
        if mode == "text":
            from app.textscript import text_script

            script = text_script(s.post)
            if s.script is not None and s.script.scenes:
                self._snapshot(job_id, s.script, "다시 쓰기 전")
            self._snapshot(job_id, script, "블로그 글 그대로")
            return self._set(job_id, mode="text", script=script, stage="ready", progress=1.0, message="",
                             failed_phase=None)
        s = self._set(job_id, mode=mode, stage="queued", progress=0.0, message="", failed_phase=None)
        self._queue.put((job_id, "script"))
        return s

    def cleanup_old(self, days: int = 7) -> int:
        limit = time.time() - days * 86400
        removed = 0
        for d in list(self.root.iterdir()):
            if not d.is_dir():
                continue
            with self._lock:
                s = self._jobs.get(d.name)
                if s and s.stage in _ACTIVE:
                    continue
                if d.stat().st_mtime >= limit:
                    continue
                self._jobs.pop(d.name, None)
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        return removed

    def stop(self, wait: bool = True) -> None:
        self._queue.put(None)
        if wait:
            self._worker.join(timeout=10)

    # ---------- 워커 ----------
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, phase = item
            try:
                self._run_one(job_id, phase)
            except Exception:  # 워커 스레드는 절대 죽지 않게
                log.exception("worker error on %s", job_id)

    def _run_one(self, job_id: str, phase: str) -> None:
        if job_id not in self._jobs:
            return
        try:
            self._check(job_id)   # 순서를 기다리는 동안 멈춘 경우
            if phase == "prepare":
                self._prepare(job_id)
            elif phase == "script":
                self._write(job_id)
            elif phase == "sources":
                self._retry_sources(job_id)
            elif phase == "shorts":
                self._shorts(job_id)
            else:
                self._render(job_id)
        except Cancelled:
            self._stopped(job_id, phase)
        except UserError as e:
            if job_id in self._cancel:   # 멈추느라 FFmpeg·음성 만들기가 실패한 것
                return self._stopped(job_id, phase)
            self._set(job_id, stage="error", message=e.message, failed_phase=phase)
        except Exception:
            if job_id in self._cancel:
                return self._stopped(job_id, phase)
            log.exception("job %s failed in %s", job_id, phase)
            self._set(job_id, stage="error", message=UNKNOWN, failed_phase=phase)

    def _prepare(self, job_id: str) -> None:
        from app.projects import merged_title, source

        s = self._set(job_id, stage="scraping", progress=0.0)
        if not s.urls:
            post = self.steps.fetch_post(s.url)
            self._set(job_id, progress=0.15)
            self._check(job_id)
            post = self.steps.download_images(post, self.dir(job_id) / "images")
            self._check(job_id)
            self._set(job_id, sources=[source(s.url, "ok", post)], name=s.name or post.title[:80])
        else:
            post, sources = self._fetch_many(job_id, s.urls, None, [])
            if post is None:
                self._set(job_id, sources=sources)
                errors = [x["message"] for x in sources if x["status"] == "error"]
                raise UserError(errors[0] if len(errors) == 1 else NO_POSTS)
            titles = [x["title"] for x in sources if x["status"] == "ok"]
            post = post.model_copy(update={"title": merged_title(titles)})
            self._set(job_id, sources=sources, name=s.name or post.title[:80])
        mode = self.get(job_id).mode
        if mode == "load":   # 불러오기만: 작성 방법은 사용자가 고른다
            self._set(job_id, post=post, stage="loaded", progress=1.0)
            return
        if mode == "manual":   # 직접 작성: AI 없이 빈 대본으로 바로 편집 (화면에서는 없앴고, 예전 작업용)
            self._set(job_id, post=post, script=blank_script(post), stage="ready", progress=1.0)
            return
        if mode == "text":     # 블로그 텍스트 그대로: 문단 → 장면, 문단 뒤 사진 → 그 장면 (AI 없음)
            from app.textscript import text_script

            script = text_script(post)
            self._snapshot(job_id, script, "블로그 글 그대로")
            self._set(job_id, post=post, script=script, stage="ready", progress=1.0)
            return
        self._set(job_id, post=post)
        self._write(job_id)

    # ---------- 여러 글 ----------
    def _fetch_many(self, job_id: str, urls: list[str], base, known: list) -> tuple:
        """글을 하나씩 불러와 붙인다. 실패한 글·같은 글은 건너뛰고 결과에 적는다."""
        from app.projects import DUP_POST, append_post, post_fingerprint, source

        post = base
        sources = []
        seen = set()
        added = 0      # 합친 글 번호는 실제로 들어간 글 기준 (빠진 글·같은 글은 세지 않음)
        if base is not None:
            seen.update(x.get("fp") for x in self.get(job_id).sources if x.get("fp"))
            added = sum(1 for x in self.get(job_id).sources if x.get("status") == "ok")
        dest = self.dir(job_id) / "images"
        for k, url in enumerate(urls):
            self._check(job_id)
            self._set(job_id, progress=round(0.9 * k / len(urls), 3))
            try:
                one = self.steps.fetch_post(url)
            except UserError as e:
                sources.append(source(url, "error", message=e.message))
                continue
            except Exception:
                log.exception("fetch failed: %s", url)
                sources.append(source(url, "error", message="글을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요."))
                continue
            fp = post_fingerprint(one)
            if fp in seen:
                sources.append(source(url, "duplicate", one, DUP_POST) | {"fp": fp})
                continue
            seen.add(fp)
            self._check(job_id)
            one = self.steps.download_images(one, dest, known=known)
            added += 1
            post = append_post(post, one, added, multi=True)
            sources.append(source(url, "ok", one) | {"fp": fp})
        return post, sources

    def retry_sources(self, job_id: str) -> JobState:
        """불러오지 못한 글만 다시 불러와 뒤에 붙인다(대본은 그대로, 새 글은 '블로그 글·사진에서 가져오기'에 보임)."""
        s = self.get(job_id)
        if s.stage in _ACTIVE or s.post is None:
            raise UserError(BUSY)
        if not any(x.get("status") == "error" for x in s.sources):
            raise UserError(NOTHING_TO_RETRY)
        self._return[job_id] = "ready" if s.script is not None else "loaded"
        s = self._set(job_id, stage="scraping", progress=0.0, message="")
        self._queue.put((job_id, "sources"))
        return s

    def _retry_sources(self, job_id: str) -> None:
        from app.scraper.naver import image_hash

        s = self.get(job_id)
        failed = [x["url"] for x in s.sources if x.get("status") == "error"]
        known = []
        for im in s.post.images:   # 이미 있는 사진과 겹치는 사진은 빼려고 지문을 모은다
            try:
                h = image_hash(Path(im.local_path).read_bytes()) if im.local_path else None
            except OSError:
                h = None
            if h is not None:
                known.append(h)
        post, results = self._fetch_many(job_id, failed, s.post, known)
        by_url = {x["url"]: x for x in results}
        sources = [by_url.get(x["url"], x) if x.get("status") == "error" else x for x in self.get(job_id).sources]
        changed = post is not s.post
        update = {"sources": sources, "stage": self._return.pop(job_id, "ready"), "progress": 1.0}
        if changed:
            update.update(post=post, analysis=None)   # 글이 늘었으니 다음 AI 작업 때 다시 분석
        self._set(job_id, **update)

    def add_photo(self, job_id: str, data: bytes, name: str = "") -> JobState:
        """사진 직접 올리기: 블로그에서 못 받은 사진이나 다른 사진을 더한다."""
        import io

        from PIL import Image, ImageOps

        from app.models import ImageRef
        from app.scraper.naver import image_hash, same_photo

        s = self.get(job_id)
        if s.stage in _ACTIVE or s.post is None:
            raise UserError(BUSY)
        h = image_hash(data)
        if h is None:
            raise UserError(BAD_PHOTO)
        for im in s.post.images:
            try:
                other = image_hash(Path(im.local_path).read_bytes()) if im.local_path else None
            except OSError:
                other = None
            if other is not None and same_photo(h, other):
                raise UserError(SAME_PHOTO)
        dest = self.dir(job_id) / "images"
        dest.mkdir(parents=True, exist_ok=True)
        n = len(list(dest.glob("upload_*.jpg")))
        path = dest / f"upload_{n:02d}.jpg"
        with Image.open(io.BytesIO(data)) as img:   # 어떤 형식이든 JPG로 (회전 정보 반영, 너무 크면 줄임)
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((4000, 4000))
            img.save(path, quality=92)
        ref = ImageRef(url=f"upload:{Path(name).name or path.name}", local_path=str(path), para=-1, post=-1)
        post = s.post.model_copy(update={"images": list(s.post.images) + [ref]})
        return self._set(job_id, post=post)

    # ---------- 유튜브 글, 썸네일 ----------
    def make_youtube(self, job_id: str) -> dict:
        s, key = self._ai_ready(job_id)
        text = self.steps.youtube_text(s.script, s.post, s.analysis, s.options, key)
        self._set(job_id, youtube=text)
        return text

    def remake_thumbnail(self, job_id: str, text: str) -> JobState:
        """썸네일 문구를 바꿔 다시 만든다 (AI 없이, 바로)."""
        from app import visuals
        from app.render import _photo_for

        s = self.get(job_id)
        if not s.video_path:
            raise UserError(NO_VIDEO)
        photo = next((p for sc in s.script.scenes for x in sc.photo_list() if (p := _photo_for(s.post, x))), None)
        channel = (self.steps.channel_name() or s.post.blog_name or "").strip()[:40]
        visuals.render_thumbnail(photo, " ".join(text.split())[:40], channel,
                                 Path(s.video_path).parent / "thumbnail.jpg", s.options.template)
        return self._set(job_id, finished_at=time.time())   # 화면이 새 썸네일을 받도록 시각을 바꾼다

    # ---------- 쇼츠 ----------
    def plan_shorts(self, job_id: str) -> VideoScript:
        s, key = self._ai_ready(job_id)
        shorts = self.steps.plan_shorts(s.script, s.post, s.analysis, key)
        self._set(job_id, shorts_script=shorts)
        return shorts

    def start_shorts(self, job_id: str, script: VideoScript) -> JobState:
        s = self.get(job_id)
        if s.stage in _ACTIVE or s.post is None:
            raise UserError(BUSY)
        if not any(x.text.strip() for x in script.scenes):
            raise UserError(NO_SHORTS)
        script = script.model_copy(update={"scenes": [x for x in script.scenes if x.text.strip()]})
        self._return[job_id] = s.stage if s.stage in ("ready", "done") else "ready"
        s = self._set(job_id, shorts_script=script, stage="queued", progress=0.0, shorts_error="", message="")
        self._queue.put((job_id, "shorts"))
        return s

    def _shorts(self, job_id: str) -> None:
        back = self._return.get(job_id, "ready")
        try:
            s = self._set(job_id, stage="tts", progress=0.0)
            voiced = self.steps.synthesize_all(s.shorts_script, s.options.voice, self.dir(job_id) / "shorts_audio",
                                               on_progress=lambda p: self._tick(job_id, round(0.2 * p, 3)),
                                               speed=s.options.speed)
            self._check(job_id)
            self._set(job_id, stage="rendering", progress=0.2)
            channel = (self.steps.channel_name() or s.post.blog_name or "").strip()[:40]
            options = s.options.model_copy(update={"aspect": "9:16", "intro": False, "outro": False,
                                                   "channel": channel})
            out = self.steps.render_video(s.post, voiced, options, self.dir(job_id) / "shorts",
                                          on_progress=lambda p: self._tick(job_id, round(0.2 + 0.8 * p, 3)))
            self._check(job_id)
            self._return.pop(job_id, None)
            self._set(job_id, stage=back, progress=1.0, shorts_script=voiced, shorts_path=str(out))
        except Cancelled:
            raise
        except UserError as e:
            if job_id in self._cancel:
                raise Cancelled() from e
            self._return.pop(job_id, None)
            self._set(job_id, stage=back, progress=1.0, shorts_error=e.message)
        except Exception:
            if job_id in self._cancel:
                raise Cancelled()
            log.exception("shorts failed for %s", job_id)
            self._return.pop(job_id, None)
            self._set(job_id, stage=back, progress=1.0, shorts_error=SHORTS_FAILED)

    # ---------- 영상 검수 ----------
    def checkup(self, job_id: str) -> dict:
        from app.ai.checkup import checkup

        s = self.get(job_id)
        if s.script is None:
            raise UserError(NO_SCRIPT)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        return checkup(s.script, s.post, s.options, self.steps.api_key(), self.steps.review)

    # ---------- 프로젝트 ----------
    def list_jobs(self) -> list[JobState]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)

    def rename(self, job_id: str, name: str) -> JobState:
        self.get(job_id)
        return self._set(job_id, name=" ".join(name.split())[:80])

    def delete(self, job_id: str) -> None:
        s = self.get(job_id)
        if s.stage in _ACTIVE:
            raise UserError(BUSY)
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def _write(self, job_id: str) -> None:
        s = self.get(job_id)
        if s.analysis is None and self.steps.analyze is not None:   # 대본 전에 여행 분석 (한 번 해 두면 다시 씀)
            self._set(job_id, stage="analyzing", progress=0.3)
            analysis = self.steps.analyze(s.post, self.steps.api_key())
            self._check(job_id)
            s = self._set(job_id, analysis=analysis.model_dump() if hasattr(analysis, "model_dump") else analysis)
        s = self._set(job_id, stage="scripting", progress=0.6)
        post, mode = s.post, s.mode
        if self.steps.write_script is not None:   # 예전 "gemini" 작업도 Claude로
            script = self.steps.write_script(post, self.steps.api_key(), s.analysis, s.settings, s.options.voice)
        else:
            script = self.steps.generate_script(post, self.steps.api_key())
        self._check(job_id)   # AI가 쓰는 동안 멈췄으면 결과를 버린다
        if s.script is not None and s.script.scenes:
            self._snapshot(job_id, s.script, "다시 쓰기 전")
        self._snapshot(job_id, script, "AI가 쓴 대본")
        self._set(job_id, script=script, stage="ready", progress=1.0)

    def _direct(self, job_id: str) -> JobState:
        """연출 요청이 새로 있으면 AI에게 물어 장면 설정(과 요청받은 자막 글)을 바꾼다."""
        from app.director import needs_direction

        s = self.get(job_id)
        if self.steps.direct is None or not needs_direction(s.script):
            return s
        self._set(job_id, stage="directing", progress=0.0)
        key = self.steps.api_key()
        if not key:
            raise UserError(NO_AI_KEY)
        was_confirmed = s.confirmed == fingerprint(s.script)
        script, options = self.steps.direct(s.script, s.options, key)
        self._check(job_id)
        # 확정한 대본에 연출 요청을 반영한 결과는 확정 상태를 이어 간다
        return self._set(job_id, script=script, options=options,
                         confirmed=fingerprint(script) if was_confirmed else s.confirmed)

    def _render(self, job_id: str) -> None:
        self._direct(job_id)
        s = self._set(job_id, stage="tts", progress=0.0)
        script = self.steps.synthesize_all(s.script, s.options.voice, self.dir(job_id) / "audio",
                                           on_progress=lambda p: self._tick(job_id, round(0.2 * p, 3)),
                                           speed=s.options.speed)
        self._check(job_id)
        self._set(job_id, script=script, stage="rendering", progress=0.2)
        channel = (self.steps.channel_name() or s.post.blog_name or "").strip()[:40]
        options = s.options.model_copy(update={"channel": channel})
        started = time.time()
        workdir = self.dir(job_id) / "render"
        out = self.steps.render_video(s.post, script, options, workdir,
                                      on_progress=lambda p: self._tick(job_id, round(0.2 + 0.8 * p, 3)))
        self._check(job_id)
        info = {"seconds": round(time.time() - started, 1)}
        try:
            info.update(json.loads((workdir / "stats.json").read_text("utf-8")))
        except (OSError, ValueError):
            pass
        self._set(job_id, stage="done", progress=1.0, video_path=str(out), finished_at=time.time(), render_info=info)
