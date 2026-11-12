from __future__ import annotations

import time
from typing import Literal

import re

from pydantic import BaseModel, Field, field_validator


class ImageRef(BaseModel):
    url: str
    local_path: str | None = None


class BlogPost(BaseModel):
    title: str
    paragraphs: list[str]
    images: list[ImageRef]
    url: str


class Scene(BaseModel):
    text: str
    image_index: int | None
    duration: float | None = None
    audio_path: str | None = None


class VideoScript(BaseModel):
    scenes: list[Scene]
    yt_title: str
    yt_description: str
    yt_tags: list[str]


class RenderOptions(BaseModel):
    aspect: Literal["16:9", "9:16"] = "16:9"
    voice: Literal["female", "male"] = "female"
    bgm: str | None = None
    bgm_volume: float = Field(default=0.15, ge=0.0, le=1.0)

    @field_validator("bgm")
    @classmethod
    def _bgm_is_plain_name(cls, v: str | None) -> str | None:
        """배경음악은 음악 폴더 안의 파일 이름만 허용 (경로·네트워크 경로 금지)."""
        if v in (None, ""):
            return None
        if re.search(r'[\\/:]', v) or v.startswith(".") or not v.lower().endswith(".mp3"):
            raise ValueError("bgm must be a music file name")
        return v


Stage = Literal["queued", "scraping", "scripting", "needs_script", "ready", "tts", "rendering", "done", "error"]
ScriptMode = Literal["api", "auto", "manual"]


class JobState(BaseModel):
    id: str
    url: str
    stage: Stage = "queued"
    mode: ScriptMode = "api"
    progress: float = 0.0
    message: str = ""
    post: BlogPost | None = None
    script: VideoScript | None = None
    options: RenderOptions = Field(default_factory=RenderOptions)
    video_path: str | None = None
    failed_phase: Literal["prepare", "render"] | None = None
    finished_at: float | None = None
    created_at: float = Field(default_factory=time.time)
