"""블로그 글 → 영상 대본 (Claude)."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import anthropic
from pydantic import BaseModel

from app.errors import UserError
from app.models import MOTIONS, TRANSITIONS, BlogPost, Scene, VideoScript

MODEL = "claude-opus-5-5"
KEY_MSG = "설정에서 API 키를 확인해 주세요."
OFFLINE = "인터넷 연결을 확인해 주세요."
FAILED = "대본을 만들지 못했어요. 다시 시도해 주세요."
NO_CREDIT = "API 크레딧이 부족해요. Anthropic 콘솔에서 결제 상태를 확인해 주세요."
BUSY = "AI 서버가 바빠요. 잠시 뒤 다시 시도해 주세요."
SLOW = "AI 응답이 늦어요. 잠시 뒤 다시 시도해 주세요."


MAX_PHOTOS = 30     # AI에게 보여 줄 사진 수 (더 많으면 고르게 뽑는다)
PHOTO_SIDE = 768    # 보여 줄 때의 긴 변 크기


class SceneDraft(BaseModel):
    text: str
    image_index: int
    place: str = ""
    motion: str = "auto"
    transition: str = "auto"


class ScriptDraft(BaseModel):
    scenes: list[SceneDraft]
    yt_title: str
    yt_description: str
    yt_tags: list[str]
    intro_title: str = ""
    intro_sub: str = ""


SYSTEM = """당신은 여행 블로그 글을 유튜브 여행 영상 대본으로 바꾸는 한국어 내레이션 작가입니다.
규칙:
- 글의 모든 내용을 처음부터 끝까지 순서대로 빠짐없이 담습니다. 요약하거나 건너뛰지 않습니다. 장면 수와 영상 길이에는 제한이 없으니, 문단마다 내용이 바뀌는 곳에서 장면을 나눠 필요한 만큼 만듭니다(보통 문단 하나에 1~3장면). 광고 문구·지도 안내·해시태그 나열처럼 읽을 내용이 아닌 부분만 뺍니다.
- 각 장면의 text는 화면 자막이자 성우가 읽을 문장입니다.
- 장면 text는 1~2문장, 40자 안팎의 자연스러운 구어체(해요체)로 씁니다. 여행의 순서(이동 → 장소 → 먹거리 → 소감)가 느껴지게 이어 주세요.
- 첫 장면은 어디로 떠난 여행인지 알려 주며 관심을 끄는 도입, 마지막 장면은 여행 소감과 구독·좋아요 부탁으로 끝냅니다.
- 원문에 없는 사실(가격, 날짜, 수치, 연락처, 장소 이름 등)을 지어내지 않습니다.
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


def _user_message(post: BlogPost) -> str:
    paras = "\n".join(f"[{i + 1}] {p}" for i, p in enumerate(post.paragraphs))
    return (f"제목: {post.title}\n원문 링크: {post.url}\n사진 개수: {len(post.images)}\n\n"
            f"본문 문단:\n{paras}")


def draft_to_script(draft: ScriptDraft, post: BlogPost) -> VideoScript:
    raw = VideoScript(
        scenes=[Scene(text=s.text, image_index=s.image_index, place=s.place or "",
                      motion=s.motion, transition=s.transition) for s in draft.scenes],
        yt_title=draft.yt_title, yt_description=draft.yt_description, yt_tags=draft.yt_tags,
        intro_title=draft.intro_title or "", intro_sub=draft.intro_sub or "")
    result = normalize_script(raw, len(post.images))
    if not result.intro_title:
        result = result.model_copy(update={"intro_title": short_title(post.title.strip() or result.yt_title)})
    return result


def photos_for_ai(post: BlogPost, limit: int = MAX_PHOTOS, side: int = PHOTO_SIDE) -> list[tuple[int, bytes]]:
    """AI에게 보여 줄 사진: (사진 번호, 작은 JPEG). 내려받은 파일이 있는 것만, 많으면 고르게 뽑는다."""
    from PIL import Image, ImageOps

    have = [i for i, im in enumerate(post.images) if im.local_path and Path(im.local_path).exists()]
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


def _claude_content(post: BlogPost):
    """글 + (있으면) 사진. 사진이 없으면 예전처럼 글만 보낸다."""
    photos = photos_for_ai(post)
    if not photos:
        return _user_message(post)
    content = [{"type": "text", "text": _user_message(post) + "\n\n아래에 사진을 번호와 함께 붙입니다."}]
    for i, data in photos:
        content.append({"type": "text", "text": f"사진 {i}번"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(data).decode("ascii")}})
    return content


def generate_script(post: BlogPost, api_key: str, client=None) -> VideoScript:
    if client is None:
        if not api_key:
            raise UserError(KEY_MSG)
        client = anthropic.Anthropic(api_key=api_key, timeout=600, max_retries=2)
    content = _claude_content(post)
    busy = False
    for _ in range(2):
        busy = False
        try:
            resp = client.beta.messages.parse(
                model=MODEL,
                max_tokens=16000,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
                output_format=ScriptDraft,
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
        if resp.stop_reason == "refusal" or draft is None or not draft.scenes:
            continue
        result = draft_to_script(draft, post)
        if result.scenes:
            return result
    raise UserError(BUSY if busy else FAILED)
