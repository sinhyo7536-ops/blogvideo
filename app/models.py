from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field


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
    bgm_volume: float = 0.15


Stage = Literal["queued", "scraping", "scripting", "ready", "tts", "rendering", "done", "error"]


class JobState(BaseModel):
    id: str
    url: str
    stage: Stage = "queued"
    progress: float = 0.0
    message: str = ""
    post: BlogPost | None = None
    script: VideoScript | None = None
    options: RenderOptions = Field(default_factory=RenderOptions)
    video_path: str | None = None
    failed_phase: Literal["prepare", "render"] | None = None
    created_at: float = Field(default_factory=time.time)
