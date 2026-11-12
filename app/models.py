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
    blog_name: str = ""


class Scene(BaseModel):
    text: str
    image_index: int | None
    duration: float | None = None
    audio_path: str | None = None
    place: str = ""  # 장소 이름표 (예: 통영 동피랑). 비면 표시 안 함


class VideoScript(BaseModel):
    scenes: list[Scene]
    yt_title: str
    yt_description: str
    yt_tags: list[str]
    intro_title: str = ""  # 시작 화면·제목 띠·썸네일에 쓰는 짧은 여행 제목
    intro_sub: str = ""    # 시작 화면 둘째 줄 (지역·기간 등)


class RenderOptions(BaseModel):
    aspect: Literal["16:9", "9:16"] = "16:9"
    voice: Literal["female", "male"] = "female"
    quality: Literal["720p", "1080p", "1440p"] = "1080p"
    bgm: str | None = None
    bgm_volume: float = Field(default=0.15, ge=0.0, le=1.0)
    template: Literal["clean", "mood", "vlog"] = "clean"   # 깔끔 / 감성 / 브이로그
    effects: Literal["auto", "soft"] = "auto"              # 전환·움직임: 다양하게 / 부드럽게
    intro: bool = True       # 시작 화면
    outro: bool = True       # 끝 화면
    title_bar: bool = True   # 제목 띠 + 장소 이름표
    channel: str = Field(default="", max_length=40)  # 끝 화면·썸네일의 채널 이름 (서버가 설정값으로 채움)

    @field_validator("bgm")
    @classmethod
    def _bgm_is_plain_name(cls, v: str | None) -> str | None:
        """배경음악은 음악 폴더 안의 파일 이름만 허용 (경로·네트워크 경로 금지)."""
        if v in (None, ""):
            return None
        if re.search(r'[\\/:]', v) or v.startswith(".") or not v.lower().endswith(".mp3"):
            raise ValueError("bgm must be a music file name")
        return v


Stage = Literal["queued", "scraping", "scripting", "ready", "tts", "rendering", "done", "error"]
ScriptMode = Literal["api", "auto", "gemini"]


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
