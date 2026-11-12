"""블로그 글 → 영상 대본 (Claude)."""
from __future__ import annotations

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


class ScriptDraft(BaseModel):
    scenes: list[SceneDraft]
    yt_title: str
    yt_description: str
    yt_tags: list[str]


SYSTEM = """당신은 블로그 글을 짧은 유튜브 영상 대본으로 바꾸는 한국어 내레이션 작가입니다.
규칙:
- 장면은 6~12개로 나눕니다. 각 장면의 text는 화면 자막이자 성우가 읽을 문장입니다.
- 장면 text는 1~2문장, 40자 안팎의 자연스러운 구어체(해요체)로 씁니다.
- 첫 장면은 시청자의 관심을 끄는 도입, 마지막 장면은 마무리 인사와 구독·좋아요 부탁으로 끝냅니다.
- 원문에 없는 사실(가격, 날짜, 수치, 연락처 등)을 지어내지 않습니다.
- image_index는 0부터 (사진 개수 - 1) 사이에서, 그 장면 내용과 가장 가까운 위치의 사진을 고릅니다. 사진이 0장이면 0을 씁니다.
- yt_title은 60자 이내, yt_description은 3~5줄 요약 뒤 마지막 줄에 원문 링크를 넣고, yt_tags는 # 없이 5~10개입니다."""


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
        scenes.append(s.model_copy(update={"text": text, "image_index": idx}))
    tags: list[str] = []
    for t in raw.yt_tags:
        t = t.strip().lstrip("#").strip()
        if t and t not in tags:
            tags.append(t)
    return VideoScript(scenes=scenes, yt_title=raw.yt_title.strip()[:60],
                       yt_description=raw.yt_description.strip(), yt_tags=tags)


def _user_message(post: BlogPost) -> str:
    paras = "\n".join(f"[{i + 1}] {p}" for i, p in enumerate(post.paragraphs))
    return (f"제목: {post.title}\n원문 링크: {post.url}\n사진 개수: {len(post.images)}\n\n"
            f"본문 문단:\n{paras}")


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
        raw = VideoScript(
            scenes=[Scene(text=s.text, image_index=s.image_index) for s in draft.scenes],
            yt_title=draft.yt_title, yt_description=draft.yt_description, yt_tags=draft.yt_tags)
        result = normalize_script(raw, len(post.images))
        if result.scenes:
            return result
    raise UserError(BUSY if busy else FAILED)
