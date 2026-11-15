"""블로그 글 → 영상 대본 (Claude)."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from app.errors import UserError
from app.models import MOTIONS, TRANSITIONS, BlogPost, Scene, VideoScript

KEY_MSG = "설정에서 API 키를 확인해 주세요."
OFFLINE = "인터넷 연결을 확인해 주세요."
FAILED = "대본을 만들지 못했어요. 다시 시도해 주세요."
NO_CREDIT = "API 크레딧이 부족해요. Anthropic 콘솔에서 결제 상태를 확인해 주세요."
BUSY = "AI 서버가 바빠요. 잠시 뒤 다시 시도해 주세요."
SLOW = "AI 응답이 늦어요. 잠시 뒤 다시 시도해 주세요."


MAX_PHOTOS = 80     # AI에게 보여 줄 사진 수 (더 많으면 고르게 뽑는다). 예전엔 30장이라 사진 많은 글에서 빠진 사진이 생겼다
PHOTO_SIDE = 768    # 보여 줄 때의 긴 변 크기
MANY_SIDE = 512     # 사진이 30장보다 많으면 작게 (요청 크기·비용)


class SceneDraft(BaseModel):
    text: str
    image_index: int
    place: str = ""
    motion: str = "auto"
    transition: str = "auto"
    section: str = ""
    paragraphs: list[int] = Field(default_factory=list)   # 이 장면 내용이 나온 문단 번호 (1부터) → 사진을 그 근처로 고정


class ScriptDraft(BaseModel):
    scenes: list[SceneDraft]
    yt_title: str
    yt_description: str
    yt_tags: list[str]
    intro_title: str = ""
    intro_sub: str = ""


SYSTEM = """당신은 여행 블로그를 실제 여행 유튜버의 영상처럼 만드는 한국어 내레이션 작가입니다. 단순 요약이 아니라, 블로거가 직접 다녀온 여행을 하나의 이야기로 풀어냅니다.

대본 구성 (각 장면의 section에 표시):
- opening: 시청자의 관심을 끄는 후킹 문구 1~2장면 (여행의 가장 흥미로운 장면이나 질문으로 시작)
- intro: 어디로, 왜, 누구와 떠난 여행인지 (글에 있는 만큼만)
- body: 시간·장소 순서에 따른 여행 이야기 (이동, 도착, 분위기)
- place: 관광지·맛집·카페 등 장소별 특징 소개
- review: 블로거가 실제로 남긴 후기·느낀 점
- ending: 여행 총평과 마무리 인사(구독·좋아요 부탁은 한 문장으로 짧게)
body·place·review는 장소 순서에 따라 섞여 나와도 됩니다.

규칙:
- 본문에 "[2번째 글] 제목" 같은 문단이 있으면 여러 블로그 글을 합친 경계 표시입니다. 그대로 읽거나 자막에 넣지 말고, 글이 바뀌는 곳(다음 날·다음 여행지)을 아는 데만 씁니다.
- 각 장면의 text는 화면 자막이자 성우가 읽을 문장입니다. 1~2문장, 40자 안팎. 문장은 성우가 읽기 좋게 짧게 끊습니다.
- 원문에 없는 사실(가격, 날짜, 수치, 연락처, 장소 이름, 메뉴)과 블로그에 없는 경험·감정을 지어내지 않습니다. 여행 분석에서 "추론"으로 표시된 내용은 단정하지 말고, 필요하면 빼거나 조심스러운 표현으로만 씁니다.
- 분량은 요청의 "분량" 지시를 따릅니다. "글 전체"이면 글의 모든 내용을 처음부터 끝까지 순서대로 빠짐없이 담고(요약·건너뛰기 금지, 광고 문구·지도 안내·해시태그 나열만 뺌), 목표 시간이 있으면 그 길이에 맞게 중요한 장소와 경험 위주로 고릅니다.
- paragraphs에는 그 장면 내용이 나온 본문 문단 번호([번호])를 적습니다. 블로그는 보통 글 몇 줄 다음에 그 글의 사진이 나오므로, image_index는 원칙적으로 그 문단 바로 뒤(또는 바로 앞)에 있는 사진("사진 위치" 참고) 중에서 고릅니다. 프로그램도 멀리 떨어진 사진은 그 문단 근처 사진으로 바꿉니다.
- 사진이 첨부되어 있으면 사진을 직접 보고, 각 장면의 내용에 가장 잘 맞는 사진의 번호를 image_index에 넣습니다. 사진은 되도록 모두 한 번씩 쓰고, 장면이 사진보다 많을 때만 이어지는 장면에 같은 사진을 다시 씁니다. 글의 흐름과 사진 순서가 어긋나지 않게 합니다. 첨부되지 않은 번호의 사진은 앞뒤 번호 사진 사이에 찍힌 것으로 보고 고릅니다.
- 사진이 첨부되지 않았으면 0부터 (사진 개수 - 1) 사이에서 그 장면 내용과 가장 가까운 위치의 번호를 고릅니다. 사진이 0장이면 0을 씁니다.
- motion은 그 사진에 어울리는 움직임입니다: zoom_in(인물·음식·간판처럼 가운데를 강조), zoom_out(넓은 풍경을 드러냄), pan_left / pan_right(옆으로 긴 풍경·거리), pan_up / pan_down(높은 건물·탑·폭포), still(글자가 많은 안내판·메뉴판). 같은 움직임이 세 번 이상 이어지지 않게 합니다.
- transition은 앞 장면에서 이 장면으로 넘어올 때의 전환입니다: fade(같은 장소에서 이어질 때), slide 또는 push(다음 장소로 이동), circle(새로운 날·새로운 주제), black(분위기가 바뀌거나 마무리), zoom(기대되는 곳에 도착), wipe(순서대로 소개할 때). 대부분은 fade를 쓰고, 장소가 바뀌는 곳에서만 다른 전환을 씁니다.
- place는 그 장면이 다루는 장소 이름입니다(예: "통영 동피랑", "해운대 해수욕장"). 15자 이내로, 원문에 나온 이름만 씁니다. 특정 장소가 아닌 장면은 빈 문자열("")로 둡니다.
- intro_title은 영상 시작 화면에 크게 넣을 여행 제목으로 16자 이내(예: "통영 1박 2일 여행"), intro_sub는 그 아래 한 줄로 지역·기간·동행 등 24자 이내입니다(원문에 없으면 빈 문자열).
- yt_title은 60자 이내, yt_description은 3~5줄 요약 뒤 마지막 줄에 원문 링크를 넣고, yt_tags는 # 없이 5~10개입니다."""


def short_title(title: str, limit: int = 22) -> str:
    """블로그 제목을 시작 화면·제목 띠에 넣기 좋게 줄인다: [말머리] 제거, ' - '·'|' 뒤 버림, 낱말 단위로 자름."""
    t = re.sub(r"^(\s*[\[\(【][^\]\)】]{0,20}[\]\)】])+\s*", "", " ".join(title.split()))
    # 앞에 붙은 날짜("2026. 10. 11.(일)", "2026-10-11")는 뺀다: 짧은 제목 자리를 날짜가 차지하지 않게
    t = re.sub(r"^\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?\s*(\([월화수목금토일]\))?\s*", "", t) or t
    t = re.split(r"\s+[-|:~]\s+|\s*\|\s*", t)[0].strip() or " ".join(title.split())
    if len(t) <= limit:
        return t
    cut = t[:limit + 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut[:limit].rstrip(" ,.-") + "…"


def normalize_script(raw: VideoScript, image_count: int) -> VideoScript:
    scenes = []
    for s in raw.scenes:
        text = " ".join(s.text.split())
        if not text:
            continue
        if image_count <= 0:
            idx = None
        elif s.image_index is not None and 0 <= s.image_index < image_count:
            idx = s.image_index
        else:
            idx = len(scenes) % image_count
        scenes.append(s.model_copy(update={"text": text, "image_index": idx,
                                            "place": " ".join((s.place or "").split())[:20]}))
    tags: list[str] = []
    for t in raw.yt_tags:
        t = t.strip().lstrip("#").strip()
        if t and t not in tags:
            tags.append(t)
    return VideoScript(scenes=scenes, yt_title=raw.yt_title.strip()[:60],
                       yt_description=raw.yt_description.strip(), yt_tags=tags,
                       intro_title=" ".join(raw.intro_title.split())[:30],
                       intro_sub=" ".join(raw.intro_sub.split())[:40])


def _user_message(post: BlogPost, extra: str = "") -> str:
    paras = "\n".join(f"[{i + 1}] {p}" for i, p in enumerate(post.paragraphs))
    where = ", ".join(f"사진 {i}=문단 {im.para + 1} 뒤" for i, im in enumerate(post.images) if im.para >= 0)
    text = (f"제목: {post.title}\n원문 링크: {post.url}\n작성 날짜: {post.date or '알 수 없음'}\n"
            f"사진 개수: {len(post.images)}\n" + (f"사진 위치: {where}\n" if where else "") + f"\n본문 문단:\n{paras}")
    return text + (f"\n\n{extra}" if extra else "")


STYLE_GUIDE = {
    "cinematic": "시네마틱 여행 다큐멘터리: 차분하고 묵직한 내레이션, 풍경과 분위기를 그림처럼 묘사, 짧고 여운 있는 문장. "
                 "합쇼체와 해요체를 섞어도 됩니다.",
    "vlog": "자연스러운 일상 브이로그: 친구에게 말하듯 편한 구어체(해요체), 그때그때의 느낌과 소소한 순간 위주.",
    "info": "여행 정보 전달형: 장소 이름·위치·특징·팁을 또렷하게 정리, 군더더기 없이 핵심 위주. "
            "가격·시간 같은 정보는 글에 있는 것만.",
    "essay": "감성적인 여행 에세이: 서정적인 표현과 비유, 여행에서 느낀 마음을 천천히 풀어 감. 과장은 피합니다.",
    "review": "맛집·관광지 리뷰형: 장소마다 무엇이 좋았고 아쉬웠는지, 추천 포인트를 분명하게. 평가는 블로거가 쓴 내용 그대로.",
}
TONE_GUIDE = {"friendly": "친근한 말투", "calm": "차분한 말투", "cheerful": "밝고 신나는 말투", "polite": "정중한 말투(합쇼체 위주)"}
DETAIL_GUIDE = {"brief": "장소는 이름과 한 줄 특징만", "normal": "장소마다 특징 2~3가지",
                "detailed": "장소마다 분위기·특징·블로거의 경험을 자세히"}
VOICE_GUIDE = {"male": "남성 성우가 읽습니다", "male_deep": "낮은 남성 목소리가 읽습니다", "hyunsu": "남성 성우가 읽습니다",
               "andrew": "남성 성우가 읽습니다", "brian": "남성 성우가 읽습니다", "florian": "남성 성우가 읽습니다"}
CHARS_PER_MIN = 330     # 성우(보통 속도)가 1분에 읽는 글자 수 (Edge TTS 실측 약 5.5자/초)


def writing_brief(settings=None, voice: str = "female", analysis=None) -> str:
    """사용자가 고른 대본 설정을 작가에게 넘길 지시문으로."""
    from app.ai.analyze import brief
    from app.models import ScriptSettings

    st = settings or ScriptSettings()
    if st.length == "auto":
        amount = "분량: 글 전체 (글의 모든 내용을 빠짐없이)"
    else:
        minutes = int(st.length)
        amount = (f"분량: 목표 영상 길이 약 {minutes}분 → 자막 글자 수 합계 약 {minutes * CHARS_PER_MIN}자 "
                  f"(±15%), 장면 수 약 {max(4, minutes * 330 // 38)}개")
    ratio = st.info_ratio
    balance = ("정보 전달 위주" if ratio >= 70 else "감성 스토리텔링 위주" if ratio <= 30 else "정보와 감성을 반반")
    lines = [
        "[대본 설정]",
        f"스타일: {STYLE_GUIDE[st.style]}",
        f"말투와 분위기: {TONE_GUIDE[st.tone]}",
        f"내레이션: {VOICE_GUIDE.get(voice, '여성 성우가 읽습니다')} — 그 목소리에 어울리는 문체로",
        f"정보 : 감성 비중 = {ratio} : {100 - ratio} ({balance})",
        f"장소 소개 상세도: {DETAIL_GUIDE[st.detail]}",
        amount,
    ]
    b = brief(analysis)
    if b:
        lines += ["", "[여행 분석 — 근거 표시를 지켜 사용하세요]", b]
    return "\n".join(lines)


def pin_photo(choice: int | None, paragraphs: list[int], post: BlogPost, used: set[int]) -> int | None:
    """장면이 나온 문단(1부터 센 번호) 근처의 사진이 아니면 근처 사진으로 바꾼다.
    근처 = 그 문단들 바로 앞 문단 뒤 ~ 바로 다음 문단 뒤에 있는 사진. 그 문단들 바로 뒤의 사진, 아직 안 쓴 사진이 먼저."""
    q = [p - 1 for p in paragraphs if 0 <= p - 1 < len(post.paragraphs)]
    if not q:
        return choice
    lo, hi = min(q), max(q)
    near = [i for i, im in enumerate(post.images) if lo - 1 <= im.para <= hi + 1]
    if not near or choice in near:
        return choice
    core = [i for i in near if lo <= post.images[i].para <= hi]
    for pool in (core, near):
        for i in pool:
            if i not in used:
                return i
    return (core or near)[0]


def draft_to_script(draft: ScriptDraft, post: BlogPost) -> VideoScript:
    used: set[int] = set()
    scenes = []
    for s in draft.scenes:
        # 오프닝(후킹)은 일부러 가장 눈길을 끄는 사진을 앞으로 가져오므로 그대로 둔다
        idx = s.image_index if s.section == "opening" else pin_photo(s.image_index, s.paragraphs, post, used)
        if idx is not None:
            used.add(idx)
        scenes.append(Scene(text=s.text, image_index=idx, place=s.place or "",
                            motion=s.motion, transition=s.transition, section=s.section))
    raw = VideoScript(
        scenes=scenes,
        yt_title=draft.yt_title, yt_description=draft.yt_description, yt_tags=draft.yt_tags,
        intro_title=draft.intro_title or "", intro_sub=draft.intro_sub or "")
    result = normalize_script(raw, len(post.images))
    # 장소 이름은 원문에 실제로 나온 것만 남긴다 (AI가 그럴듯한 이름을 지어내 모든 장면에 붙인 적이 있다)
    source = (post.title + " " + " ".join(post.paragraphs)).replace(" ", "")
    scenes = [s if not s.place or all(w in source for w in s.place.split()) else s.model_copy(update={"place": ""})
              for s in result.scenes]
    result = result.model_copy(update={"scenes": scenes})
    if not result.intro_title:
        result = result.model_copy(update={"intro_title": short_title(post.title.strip() or result.yt_title)})
    return result


def photos_for_ai(post: BlogPost, limit: int = MAX_PHOTOS, side: int | None = None) -> list[tuple[int, bytes]]:
    """AI에게 보여 줄 사진: (사진 번호, 작은 JPEG). 내려받은 파일이 있는 것만, 많으면 고르게 뽑는다."""
    from PIL import Image, ImageOps

    have = [i for i, im in enumerate(post.images) if im.local_path and Path(im.local_path).exists()]
    if side is None:
        side = PHOTO_SIDE if len(have) <= 30 else MANY_SIDE
    if len(have) > limit:
        step = len(have) / limit
        have = [have[int(k * step)] for k in range(limit)]
    out = []
    for i in have:
        try:
            with Image.open(post.images[i].local_path) as im:
                img = ImageOps.exif_transpose(im).convert("RGB")
                img.thumbnail((side, side))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=72)
        except OSError:
            continue
        out.append((i, buf.getvalue()))
    return out


def _claude_content(post: BlogPost, extra: str = ""):
    """글 + (있으면) 사진. 사진이 없으면 예전처럼 글만 보낸다."""
    photos = photos_for_ai(post)
    if not photos:
        return _user_message(post, extra)
    content = [{"type": "text", "text": _user_message(post, extra) + "\n\n아래에 사진을 번호와 함께 붙입니다."}]
    for i, data in photos:
        content.append({"type": "text", "text": f"사진 {i}번"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(data).decode("ascii")}})
    return content


def ask_claude(system: str, content, output_format, api_key: str, client=None, accept=None, task: str = "script"):
    """Claude에게 물어 정해진 형식(output_format)의 답을 받는다. accept(답)이 거짓이면 한 번 더 묻는다.
    task: 작업 이름 (app.ai.models.TASKS). 작업마다 설정된 모델·생각 깊이를 쓴다."""
    from app.ai.models import effort_for, model_for, supports_fallback

    model = model_for(task)
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if supports_fallback(model) else {}
    if client is None:
        if not api_key:
            raise UserError(KEY_MSG)
        client = anthropic.Anthropic(api_key=api_key, timeout=600, max_retries=2)
    busy = False
    for _ in range(2):
        busy = False
        try:
            resp = client.beta.messages.parse(
                model=model,
                max_tokens=16000,
                output_config={"effort": effort_for(task)},
                **extra,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output_format,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise UserError(KEY_MSG) from e
        except anthropic.APITimeoutError as e:
            raise UserError(SLOW) from e
        except anthropic.APIConnectionError as e:
            raise UserError(OFFLINE) from e
        except anthropic.RateLimitError as e:
            raise UserError(BUSY) from e
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower() or "billing" in str(e).lower():
                raise UserError(NO_CREDIT) from e
            raise UserError(FAILED) from e
        except anthropic.NotFoundError as e:
            raise UserError(FAILED) from e
        except anthropic.APIStatusError as e:
            if e.status_code in (529, 503):
                busy = True
                continue
            continue
        draft = getattr(resp, "parsed_output", None)
        if resp.stop_reason == "refusal" or draft is None:
            continue
        if accept is None or accept(draft):
            return draft
    raise UserError(BUSY if busy else FAILED)


def generate_script(post: BlogPost, api_key: str, client=None, analysis=None, settings=None,
                    voice: str = "female") -> VideoScript:
    extra = writing_brief(settings, voice, analysis) if (analysis or settings) else ""
    draft = ask_claude(SYSTEM, _claude_content(post, extra), ScriptDraft, api_key, client,
                       accept=lambda d: bool(d.scenes) and bool(draft_to_script(d, post).scenes))
    return draft_to_script(draft, post)
