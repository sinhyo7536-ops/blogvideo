from __future__ import annotations

import time
from typing import Literal

import re

from pydantic import BaseModel, Field, field_validator


class ImageRef(BaseModel):
    url: str
    local_path: str | None = None
    para: int = -1          # 이 사진 바로 앞의 문단 번호(-1이면 첫 문단보다 앞). 본문과 사진을 잇는 단서
    post: int = 0           # 여러 게시글을 합쳤을 때 몇 번째 글의 사진인지


class BlogPost(BaseModel):
    title: str
    paragraphs: list[str]
    images: list[ImageRef]
    url: str
    blog_name: str = ""
    date: str = ""                                   # 작성 날짜 (예: 2026. 11. 4. 10:22)
    missing: list[str] = Field(default_factory=list)  # 받지 못한 사진 설명 (사용자에게 알린다)
    duplicates: int = 0                              # 같은 사진이라 뺀 수


MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "still")
TRANSITIONS = ("fade", "slide", "push", "circle", "black", "zoom", "wipe")
LOOKS = ("warm", "cool", "bw", "vivid", "soft")              # 색감
CAPTION_SIZES = ("small", "large")
CAPTION_POSITIONS = ("top", "center", "bottom")
STRENGTHS = ("subtle", "strong")                               # 움직임 세기
MAX_HOLD = 5.0                                                 # 목소리가 끝난 뒤 더 머무는 시간(초)
MAX_MORE_IMAGES = 6                                            # 한 장면에 함께 보여 줄 사진 (첫 사진 말고)
MAX_PHOTO_SEC = 30.0


VOICE_KEYS = ("female", "female_bright", "female_calm", "male", "male_deep", "hyunsu",
              "ava", "emma", "vivienne", "seraphina", "andrew", "brian", "florian")


SECTIONS = ("opening", "intro", "body", "place", "review", "ending")
SECTION_LABELS = {"opening": "오프닝", "intro": "여행 소개", "body": "여행 이야기", "place": "장소 소개",
                  "review": "후기", "ending": "엔딩"}


class Scene(BaseModel):
    text: str
    image_index: int | None
    duration: float | None = None
    audio_path: str | None = None
    place: str = ""  # 장소 이름표 (예: 통영 동피랑). 비면 표시 안 함
    section: str = ""  # 대본 구성: SECTIONS 중 하나 (비면 표시 안 함)
    motion: str = "auto"      # 사진 움직임: auto 또는 MOTIONS 중 하나
    transition: str = "auto"  # 이 장면으로 들어올 때의 전환: auto 또는 TRANSITIONS 중 하나

    # --- 연출: 사용자가 글로 요청하면 AI가 아래 값을 정한다 (app/director.py) ---
    direction: str = Field(default="", max_length=400)        # 이 장면의 연출 요청
    direction_done: str = Field(default="", max_length=400)   # 이미 반영한 요청(같으면 다시 AI에 묻지 않음)
    look: str = "auto"            # 색감: auto(그대로) 또는 LOOKS
    caption_size: str = "auto"    # 자막 크기: auto 또는 CAPTION_SIZES
    caption_pos: str = "auto"     # 자막 위치: auto(아래) 또는 CAPTION_POSITIONS
    strength: str = "auto"        # 움직임 세기: auto 또는 STRENGTHS
    hold: float = 0.0             # 더 머무는 시간(초)

    # --- 사진 (6단계) ---
    more_images: list[int] = Field(default_factory=list)   # 첫 사진(image_index) 뒤에 이어 보여 줄 사진
    photo_secs: list[float] = Field(default_factory=list)  # [첫 사진, 이어지는 사진…] 각각 보여 줄 시간(초). 0 = 자동
    photo_score: int = -1         # AI가 본 '글과 사진이 맞는 정도' 0~100 (-1 = 모름)
    photo_alts: list[int] = Field(default_factory=list)    # AI가 추천한 사진 (좋은 순)

    @field_validator("more_images", mode="before")
    @classmethod
    def _more_images(cls, v):
        out = []
        for x in v or []:
            if isinstance(x, int) and x >= 0 and x not in out:
                out.append(x)
        return out[:MAX_MORE_IMAGES]

    @field_validator("photo_secs", mode="before")
    @classmethod
    def _photo_secs(cls, v):
        out = []
        for x in (v or [])[:MAX_MORE_IMAGES + 1]:
            try:
                out.append(round(min(MAX_PHOTO_SEC, max(0.0, float(x))), 2))
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    @field_validator("photo_score", mode="before")
    @classmethod
    def _photo_score(cls, v):
        try:
            return max(-1, min(100, int(v)))
        except (TypeError, ValueError):
            return -1

    @field_validator("photo_alts", mode="before")
    @classmethod
    def _photo_alts(cls, v):
        return [x for x in (v or []) if isinstance(x, int) and x >= 0][:4]

    def photo_list(self) -> list[int]:
        """이 장면에 보여 줄 사진 번호 (첫 사진 + 이어지는 사진)."""
        first = [] if self.image_index is None else [self.image_index]
        return first + [x for x in self.more_images if x not in first]

    @field_validator("section", mode="before")
    @classmethod
    def _known_section(cls, v):
        return v if v in SECTIONS else ""

    @field_validator("look", mode="before")
    @classmethod
    def _known_look(cls, v):
        return v if v in LOOKS else "auto"

    @field_validator("caption_size", mode="before")
    @classmethod
    def _known_caption_size(cls, v):
        return v if v in CAPTION_SIZES else "auto"

    @field_validator("caption_pos", mode="before")
    @classmethod
    def _known_caption_pos(cls, v):
        return v if v in CAPTION_POSITIONS else "auto"

    @field_validator("strength", mode="before")
    @classmethod
    def _known_strength(cls, v):
        return v if v in STRENGTHS else "auto"

    @field_validator("hold", mode="before")
    @classmethod
    def _sane_hold(cls, v):
        try:
            return round(min(MAX_HOLD, max(0.0, float(v))), 2)
        except (TypeError, ValueError):
            return 0.0

    @field_validator("motion", mode="before")
    @classmethod
    def _known_motion(cls, v):
        return v if v in MOTIONS else "auto"

    @field_validator("transition", mode="before")
    @classmethod
    def _known_transition(cls, v):
        return v if v in TRANSITIONS else "auto"


class VideoScript(BaseModel):
    scenes: list[Scene]
    yt_title: str
    yt_description: str
    yt_tags: list[str]
    intro_title: str = ""  # 시작 화면·제목 띠·썸네일에 쓰는 짧은 여행 제목
    intro_sub: str = ""    # 시작 화면 둘째 줄 (지역·기간 등)
    direction: str = Field(default="", max_length=600)        # 영상 전체에 대한 연출 요청
    direction_done: str = Field(default="", max_length=600)
    excluded_images: list[int] = Field(default_factory=list)   # 영상에 쓰지 않기로 한 사진 (AI 매칭도 안 씀)


class RenderOptions(BaseModel):
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    voice: str = "female"                                   # VOICE_KEYS 중 하나 (목록은 app/tts.py)
    speed: Literal["slow", "normal", "fast"] = "normal"     # 말하는 속도
    quality: Literal["720p", "1080p", "1440p"] = "1080p"
    bgm: str | None = None
    bgm_volume: float = Field(default=0.15, ge=0.0, le=1.0)
    template: Literal["clean", "mood", "vlog", "cinema", "essay"] = "clean"   # 깔끔 / 감성 / 브이로그 / 시네마 / 에세이
    preset: Literal["custom", "cinematic", "vlog", "info", "essay"] = "custom"  # 마지막으로 고른 영상 프리셋
    effects: Literal["auto", "soft"] = "auto"              # 전환·움직임: 다양하게 / 부드럽게
    intro: bool = True       # 시작 화면
    outro: bool = True       # 끝 화면
    title_bar: bool = True   # 제목 띠 + 장소 이름표
    channel: str = Field(default="", max_length=40)  # 끝 화면·썸네일의 채널 이름 (서버가 설정값으로 채움)

    @field_validator("voice")
    @classmethod
    def _known_voice(cls, v: str) -> str:
        if v not in VOICE_KEYS:
            raise ValueError("unknown voice")
        return v

    @field_validator("bgm")
    @classmethod
    def _bgm_is_plain_name(cls, v: str | None) -> str | None:
        """배경음악은 음악 폴더 안의 파일 이름만 허용 (경로·네트워크 경로 금지)."""
        if v in (None, ""):
            return None
        if re.search(r'[\\/:]', v) or v.startswith(".") or not v.lower().endswith(".mp3"):
            raise ValueError("bgm must be a music file name")
        return v


Stage = Literal["queued", "scraping", "loaded", "analyzing", "scripting", "ready", "directing", "tts", "rendering",
                "done", "error"]
# load: 글·사진만 불러온 상태(아직 작성 방법을 고르지 않음), manual: 직접 작성
# auto("AI 없이")와 gemini(제미나이)는 없앴다. 예전에 저장된 작업을 열 수 있게 값만 남겨 둔다.
ScriptMode = Literal["api", "auto", "gemini", "load", "manual"]


SCRIPT_STYLES = {
    "cinematic": "시네마틱 여행 다큐멘터리",
    "vlog": "자연스러운 일상 브이로그",
    "info": "여행 정보 전달형",
    "essay": "감성적인 여행 에세이",
    "review": "맛집·관광지 리뷰형",
}


class ScriptSettings(BaseModel):
    """대본을 쓸 때 사용자가 정하는 것."""
    style: Literal["cinematic", "vlog", "info", "essay", "review"] = "vlog"
    length: Literal["auto", "1", "3", "5", "10"] = "auto"     # 목표 영상 길이(분). auto = 글 내용을 모두
    tone: Literal["friendly", "calm", "cheerful", "polite"] = "friendly"
    info_ratio: int = Field(default=50, ge=0, le=100)         # 0 = 감성 이야기 위주, 100 = 정보 위주
    detail: Literal["brief", "normal", "detailed"] = "normal"  # 장소 소개 상세도


class JobState(BaseModel):
    id: str
    url: str
    name: str = Field(default="", max_length=80)        # 프로젝트 이름 (비면 글 제목)
    urls: list[str] = Field(default_factory=list)       # 합쳐서 만드는 글 주소들 (비면 url 하나)
    sources: list[dict] = Field(default_factory=list)   # 글마다 불러온 결과 (app.projects.source)
    stage: Stage = "queued"
    mode: ScriptMode = "api"
    progress: float = 0.0
    message: str = ""
    post: BlogPost | None = None
    script: VideoScript | None = None
    analysis: dict | None = None        # AI 여행 분석 (app.ai.analyze.TravelAnalysis)
    settings: ScriptSettings = Field(default_factory=ScriptSettings)
    youtube: dict | None = None         # AI가 쓴 유튜브 글 (제목 후보·설명·해시태그·썸네일 문구·키워드·챕터)
    shorts_script: VideoScript | None = None   # 쇼츠 대본 (사용자가 확인·수정)
    shorts_path: str | None = None      # 만든 쇼츠 영상
    shorts_error: str = ""              # 쇼츠를 만들지 못한 까닭 (본 영상 화면에 보여 줌)
    render_info: dict | None = None     # 마지막 영상 만들기: 걸린 시간, 장면 조각 수, 다시 쓴 조각 수
    confirmed: str = ""                 # 사용자가 '대본 확정'을 누른 대본의 지문 (고치면 달라져 다시 확정해야 함)
    options: RenderOptions = Field(default_factory=RenderOptions)
    video_path: str | None = None
    failed_phase: Literal["prepare", "script", "render"] | None = None
    finished_at: float | None = None
    created_at: float = Field(default_factory=time.time)
