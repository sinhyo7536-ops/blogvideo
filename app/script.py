"""블로그 글 → 영상 대본 (Claude)."""
from __future__ import annotations

import re

import anthropic
from pydantic import BaseModel

from app.errors import UserError
from app.models import BlogPost, Scene, VideoScript

MODEL = "claude-opus-5-5"
KEY_MSG = "설정에서 API 키를 확인해 주세요."
OFFLINE = "인터넷 연결을 확인해 주세요."
FAILED = "대본을 만들지 못했어요. 다시 시도해 주세요."
NO_CREDIT = "API 크레딧이 부족해요. Anthropic 콘솔에서 결제 상태를 확인해 주세요."
BUSY = "AI 서버가 바빠요. 잠시 뒤 다시 시도해 주세요."
SLOW = "AI 응답이 늦어요. 잠시 뒤 다시 시도해 주세요."


class SceneDraft(BaseModel):
    text: str
    image_index: int
    place: str = ""


class ScriptDraft(BaseModel):
    scenes: list[SceneDraft]
    yt_title: str
    yt_description: str
    yt_tags: list[str]
    intro_title: str = ""
    intro_sub: str = ""


SYSTEM = """당신은 여행 블로그 글을 유튜브 여행 영상 대본으로 바꾸는 한국어 내레이션 작가입니다.
규칙:
- 장면은 6~12개로 나눕니다. 각 장면의 text는 화면 자막이자 성우가 읽을 문장입니다.
- 장면 text는 1~2문장, 40자 안팎의 자연스러운 구어체(해요체)로 씁니다. 여행의 순서(이동 → 장소 → 먹거리 → 소감)가 느껴지게 이어 주세요.
- 첫 장면은 어디로 떠난 여행인지 알려 주며 관심을 끄는 도입, 마지막 장면은 여행 소감과 구독·좋아요 부탁으로 끝냅니다.
- 원문에 없는 사실(가격, 날짜, 수치, 연락처, 장소 이름 등)을 지어내지 않습니다.
- image_index는 0부터 (사진 개수 - 1) 사이에서, 그 장면 내용과 가장 가까운 위치의 사진을 고릅니다. 사진이 0장이면 0을 씁니다.
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
        scenes=[Scene(text=s.text, image_index=s.image_index, place=s.place or "") for s in draft.scenes],
        yt_title=draft.yt_title, yt_description=draft.yt_description, yt_tags=draft.yt_tags,
        intro_title=draft.intro_title or "", intro_sub=draft.intro_sub or "")
    result = normalize_script(raw, len(post.images))
    if not result.intro_title:
        result = result.model_copy(update={"intro_title": short_title(post.title.strip() or result.yt_title)})
    return result


def generate_script(post: BlogPost, api_key: str, client=None) -> VideoScript:
    if client is None:
        if not api_key:
            raise UserError(KEY_MSG)
        client = anthropic.Anthropic(api_key=api_key, timeout=180, max_retries=2)
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
                messages=[{"role": "user", "content": _user_message(post)}],
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
