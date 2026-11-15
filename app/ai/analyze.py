"""AI 여행 분석: 블로그 글과 사진을 읽고 '여행 이야기'의 뼈대를 만든다 (대본을 쓰기 전 단계).

결과는 작업에 저장되어 대본 쓰기·사진 매칭·쇼츠·검수가 다시 쓴다. 블로그에 없는 경험을 만들지 않도록
모든 항목에 근거(글에 나옴 / 사진에서 봄 / 추론)를 붙이고, 모르는 것은 '빠진 정보'로 남긴다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models import BlogPost

PHOTO_CATEGORIES = ("풍경", "음식", "카페", "관광지", "건축물", "인물", "이동·교통", "숙소", "기타")
PLACE_KINDS = ("관광지", "맛집", "카페", "숙소", "자연", "시장·쇼핑", "이동", "기타")
EVIDENCE = ("글에 나옴", "사진에서 봄", "추론")


class PlaceInfo(BaseModel):
    name: str
    kind: str = "기타"
    day: int = 0                                   # 몇째 날 (모르면 0)
    summary: str = ""                              # 어떤 곳인지 (글에 나온 범위에서)
    experience: str = ""                           # 블로거가 실제로 한 일·느낀 점 (글에 있는 것만)
    paragraphs: list[int] = Field(default_factory=list)   # 관련 문단 번호 (0부터)
    photos: list[int] = Field(default_factory=list)       # 관련 사진 번호 (0부터)
    evidence: str = "글에 나옴"

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        return v if v in PLACE_KINDS else "기타"

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, v):
        return v if v in EVIDENCE else "추론"


class DayPlan(BaseModel):
    day: int
    date: str = ""
    summary: str = ""
    places: list[str] = Field(default_factory=list)


class PhotoInfo(BaseModel):
    index: int
    category: str = "기타"
    description: str = ""                          # 사진에 보이는 것 (한 문장)
    place: str = ""                                # 어느 장소의 사진인지 (알 때만)
    paragraph: int = -1                            # 가장 관련 있는 문단 번호
    highlight: bool = False                        # 영상에서 특히 눈길을 끌 만한 사진

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return v if v in PHOTO_CATEGORIES else "기타"


class Note(BaseModel):
    text: str
    evidence: str = "글에 나옴"

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, v):
        return v if v in EVIDENCE else "추론"


class TravelAnalysis(BaseModel):
    theme: str = ""                                # 여행의 전체 주제 (한 문장)
    region: str = ""
    period: str = ""                               # 기간·날짜 (글에 있을 때만)
    companions: str = ""                           # 동행 (글에 있을 때만)
    start: str = ""                                # 여행 시작 지점
    end: str = ""                                  # 여행 마지막 지점
    days: list[DayPlan] = Field(default_factory=list)
    places: list[PlaceInfo] = Field(default_factory=list)
    highlights: list[Note] = Field(default_factory=list)    # 인상적인 경험
    episodes: list[Note] = Field(default_factory=list)      # 여행 중 있었던 일
    best_moment: str = ""                          # 영상에서 가장 흥미로운 장면 (쇼츠 후보)
    photos: list[PhotoInfo] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)   # 글에 없어서 알 수 없는 것
    tone: str = ""                                 # 글의 분위기 (밝음 / 차분함 / 감성적 / 정보 위주 / 유쾌함)


# AI에게 보내는 형식: 모든 항목을 '반드시 채움'으로 둔다. 선택 항목(기본값)이 많으면 API가
# "Schema is too complex" 로 거절한다(2026-11-15 실제 호출에서 확인). 받은 뒤 위 모델로 옮겨 담는다.
class _WPlace(BaseModel):
    name: str
    kind: str
    day: int
    summary: str
    experience: str
    paragraphs: list[int]
    photos: list[int]
    evidence: str


class _WDay(BaseModel):
    day: int
    date: str
    summary: str
    places: list[str]


class _WPhoto(BaseModel):
    index: int
    category: str
    description: str
    place: str
    paragraph: int
    highlight: bool


class _WNote(BaseModel):
    text: str
    evidence: str


class AnalysisDraft(BaseModel):
    theme: str
    region: str
    period: str
    companions: str
    start: str
    end: str
    days: list[_WDay]
    places: list[_WPlace]
    highlights: list[_WNote]
    episodes: list[_WNote]
    best_moment: str
    photos: list[_WPhoto]
    missing_info: list[str]
    tone: str


SYSTEM = f"""당신은 여행 블로그를 분석하는 편집자입니다. 블로그 글과 사진을 읽고, 여행 영상을 만들 때 쓸 '여행 이야기의 뼈대'를 정리합니다.

반드시 지킬 것:
- 블로그에 적혀 있지 않은 경험·감정·사실(가격, 시간, 메뉴, 날씨, 동행 등)을 만들어 내지 않습니다.
- 모든 장소·경험·에피소드에 근거를 붙입니다: "글에 나옴"(본문에 적혀 있음), "사진에서 봄"(사진으로만 확인), "추론"(앞뒤 내용으로 짐작). 추론은 꼭 필요할 때만 쓰고, 근거가 약하면 빼거나 missing_info 에 적습니다.
- 본문에 "[2번째 글] 제목" 같은 문단이 있으면 여러 블로그 글을 합친 경계 표시입니다. 그대로 읽거나 자막에 넣지 말고, 글이 바뀌는 곳(다음 날·다음 여행지)을 아는 데만 씁니다.
- 모르는 것은 빈 문자열로 두고 missing_info 에 "방문 날짜가 글에 없음"처럼 적습니다.

정리할 것:
- theme(여행 주제 한 문장), region, period, companions, start(출발·첫 장소), end(마지막 장소)
- days: 날짜별(몇째 날) 일정. 날짜를 구분할 수 없으면 day 1 하나로 묶습니다.
- places: 방문한 장소마다 name(원문 표기 그대로), kind({" / ".join(PLACE_KINDS)}), day, summary(어떤 곳인지), experience(블로거가 실제로 한 일·느낀 점), paragraphs(관련 문단 번호, 0부터), photos(관련 사진 번호, 0부터), evidence
- highlights: 인상적인 경험, episodes: 여행 중 있었던 일(헤맸던 일, 우연히 만난 것 등) — 글에 있는 것만
- best_moment: 영상에서 가장 흥미로울 장면 한 가지
- photos: 사진마다 index, category({" / ".join(PHOTO_CATEGORIES)}), description(보이는 것 한 문장), place(알 때만), paragraph(가장 관련 있는 문단 번호), highlight(특히 눈길을 끄는 사진이면 true)
  사진 번호마다 "문단 k 뒤에 있음"이라는 위치 정보가 주어지면, 그 문단과 앞뒤 문단을 우선 연결합니다. 예: 식당을 설명하는 문단 뒤의 음식 사진은 그 식당과 연결합니다.
- tone: 글의 분위기 (밝음 / 차분함 / 감성적 / 정보 위주 / 유쾌함)"""


def _request(post: BlogPost) -> str:
    paras = "\n".join(f"[{i}] {p}" for i, p in enumerate(post.paragraphs))
    where = "\n".join(f"사진 {i}: " + (f"문단 {im.para} 뒤" if im.para >= 0 else "첫 문단 앞")
                      for i, im in enumerate(post.images))
    head = f"제목: {post.title}\n작성 날짜: {post.date or '알 수 없음'}\n사진 {len(post.images)}장\n"
    if post.missing:
        head += f"받지 못한 사진: {', '.join(post.missing)} (이 사진들은 없다고 보고 분석)\n"
    return f"{head}\n본문 문단(번호는 0부터):\n{paras}\n\n사진 위치:\n{where or '(사진 없음)'}"


def clean(a: TravelAnalysis, post: BlogPost) -> TravelAnalysis:
    """번호가 범위를 벗어난 것을 정리하고, 사진 정보가 빠진 사진을 채운다."""
    n_p, n_i = len(post.paragraphs), len(post.images)
    places = [p.model_copy(update={"paragraphs": [x for x in p.paragraphs if 0 <= x < n_p],
                                   "photos": [x for x in p.photos if 0 <= x < n_i],
                                   "name": " ".join(p.name.split())[:40]})
              for p in a.places if p.name.strip()]
    photos = {p.index: p for p in a.photos if 0 <= p.index < n_i}
    for i, im in enumerate(post.images):   # AI가 빠뜨린 사진은 위치 정보만으로 채운다
        if i not in photos:
            photos[i] = PhotoInfo(index=i, paragraph=im.para)
        elif not 0 <= photos[i].paragraph < n_p:
            photos[i] = photos[i].model_copy(update={"paragraph": im.para})
    return a.model_copy(update={"places": places, "photos": [photos[i] for i in sorted(photos)]})


def analyze(post: BlogPost, api_key: str, client=None) -> TravelAnalysis:
    from app.script import ask_claude, photos_for_ai
    import base64

    photos = photos_for_ai(post)
    if photos:
        content = [{"type": "text", "text": _request(post) + "\n\n아래에 사진을 번호와 함께 붙입니다."}]
        for i, data in photos:
            content.append({"type": "text", "text": f"사진 {i}번"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(data).decode("ascii")}})
    else:
        content = _request(post)
    answer = ask_claude(SYSTEM, content, AnalysisDraft, api_key, client, task="analysis",
                        accept=lambda a: bool(a.theme or a.places))
    return clean(TravelAnalysis.model_validate(answer.model_dump()), post)


def brief(a: TravelAnalysis | dict | None) -> str:
    """대본 작가에게 넘길 분석 요약 (JSON 그대로보다 짧게)."""
    if not a:
        return ""
    if isinstance(a, dict):
        a = TravelAnalysis.model_validate(a)
    lines = [f"주제: {a.theme}", f"지역: {a.region}", f"기간: {a.period or '글에 없음'}",
             f"동행: {a.companions or '글에 없음'}", f"시작: {a.start} / 끝: {a.end}"]
    for d in a.days:
        lines.append(f"{d.day}일차 {d.date}: {d.summary} ({', '.join(d.places)})")
    for p in a.places:
        lines.append(f"- 장소 {p.name} [{p.kind}, {p.day}일차, {p.evidence}] {p.summary} / 경험: {p.experience or '글에 없음'}"
                     f" / 문단 {p.paragraphs} / 사진 {p.photos}")
    for h in a.highlights:
        lines.append(f"- 인상적인 경험({h.evidence}): {h.text}")
    for e in a.episodes:
        lines.append(f"- 에피소드({e.evidence}): {e.text}")
    if a.best_moment:
        lines.append(f"가장 흥미로운 장면: {a.best_moment}")
    for ph in a.photos:
        lines.append(f"사진 {ph.index}: [{ph.category}] {ph.description} (장소 {ph.place or '?'}, 문단 {ph.paragraph})")
    if a.missing_info:
        lines.append("글에 없어서 모르는 것: " + "; ".join(a.missing_info))
    return "\n".join(lines)
