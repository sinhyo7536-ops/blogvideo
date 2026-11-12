"""작업(job) 관리: 큐, 상태 저장, 단계 실행."""
from __future__ import annotations

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
from app.autoscript import auto_script
from app.models import JobState, RenderOptions, VideoScript

log = logging.getLogger(__name__)

UNKNOWN = "알 수 없는 오류가 났어요. 다시 시도해 주세요."
INTERRUPTED = "작업이 중간에 멈췄어요. 다시 시도해 주세요."
BUSY = "지금은 바꿀 수 없어요. 진행 중인 작업이 끝난 뒤 다시 해 주세요."
NO_SCRIPT = "대본이 아직 없어요."

_ACTIVE = {"queued", "scraping", "scripting", "tts", "rendering"}


@dataclass
class Steps:
    fetch_post: Callable
    download_images: Callable
    generate_script: Callable
    synthesize_all: Callable
    render_video: Callable
    api_key: Callable[[], str]
    auto_script: Callable = auto_script


class JobManager:
    def __init__(self, root: Path, steps: Steps):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.steps = steps
        self._lock = threading.RLock()
        self._jobs: dict[str, JobState] = {}
        self._queue: queue.Queue = queue.Queue()
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
                phase = "render" if s.stage in ("tts", "rendering") or (s.stage == "queued" and s.script) else "prepare"
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

    def get(self, job_id: str) -> JobState:
        with self._lock:
            return self._jobs[job_id]

    def dir(self, job_id: str) -> Path:
        return self.root / job_id

    # ---------- 공개 동작 ----------
    def create(self, url: str, options: RenderOptions, mode: str = "api") -> JobState:
        s = JobState(id=uuid.uuid4().hex[:12], url=url, options=options, mode=mode)
        with self._lock:
            self._jobs[s.id] = s
            self._save(s)
        self._queue.put((s.id, "prepare"))
        return s

    def update_script(self, job_id: str, script: VideoScript, options: RenderOptions) -> JobState:
        s = self.get(job_id)
        if s.stage in _ACTIVE or s.post is None:
            raise UserError(BUSY)
        return self._set(job_id, script=script, options=options, stage="ready", message="", progress=1.0)

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

    def use_free_mode(self, job_id: str, mode: str) -> JobState:
        """API 대본이 실패했을 때 무료 방법으로 이어서 하기."""
        s = self.get(job_id)
        if s.post is None or s.stage in _ACTIVE or mode != "auto":
            raise UserError(BUSY)
        return self._set(job_id, mode="auto", script=self.steps.auto_script(s.post), stage="ready",
                         progress=1.0, message="", failed_phase=None)

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
            if phase == "prepare":
                self._prepare(job_id)
            else:
                self._render(job_id)
        except UserError as e:
            self._set(job_id, stage="error", message=e.message, failed_phase=phase)
        except Exception:
            log.exception("job %s failed in %s", job_id, phase)
            self._set(job_id, stage="error", message=UNKNOWN, failed_phase=phase)

    def _prepare(self, job_id: str) -> None:
        s = self._set(job_id, stage="scraping", progress=0.0)
        post = self.steps.fetch_post(s.url)
        self._set(job_id, progress=0.15)
        post = self.steps.download_images(post, self.dir(job_id) / "images")
        self._set(job_id, post=post, stage="scripting", progress=0.3)
        if self.get(job_id).mode == "auto":
            script = self.steps.auto_script(post)
        else:
            script = self.steps.generate_script(post, self.steps.api_key())
        self._set(job_id, script=script, stage="ready", progress=1.0)

    def _render(self, job_id: str) -> None:
        s = self._set(job_id, stage="tts", progress=0.0)
        script = self.steps.synthesize_all(s.script, s.options.voice, self.dir(job_id) / "audio",
                                           on_progress=lambda p: self._set(job_id, progress=round(0.2 * p, 3)))
        self._set(job_id, script=script, stage="rendering", progress=0.2)
        out = self.steps.render_video(s.post, script, s.options, self.dir(job_id) / "render",
                                      on_progress=lambda p: self._set(job_id, progress=round(0.2 + 0.8 * p, 3)))
        self._set(job_id, stage="done", progress=1.0, video_path=str(out), finished_at=time.time())
