"""대본 편집기의 AI 도구: 고른 문단만 다시 쓰기(명령), 맞춤법·문맥 검토.

두 기능 모두 **제안만** 돌려준다. 적용은 사용자가 화면에서 전후를 비교한 뒤 고른다.
고르지 않은 문단은 절대 바뀌지 않는다(서버가 결과에서 걸러 낸다).
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from app.models import BlogPost, VideoScript

# 빠른 명령 버튼 (화면에 보이는 이름, AI에게 보내는 지시)
PRESETS = {
    "natural": ("자연스럽게", "성우가 읽기 자연스러운 구어체로 다듬어 주세요. 뜻은 그대로 둡니다."),
    "youtuber": ("유튜버 말투", "여행 유튜버가 시청자에게 말하듯 생동감 있게 바꿔 주세요."),
    "emotion": ("감성 더하기", "분위기와 느낌이 전해지도록 감성을 조금 더 담아 주세요. 글에 없는 감정은 만들지 않습니다."),
    "plain": ("과장 줄이기", "과장된 표현과 감탄사를 줄이고 담백하게 바꿔 주세요."),
    "shorter": ("30% 줄이기", "내용의 핵심은 남기고 글자 수를 30% 정도 줄여 주세요."),
    "longer": ("조금 길게", "원문 블로그에 있는 내용으로만 조금 더 자세하게 늘려 주세요."),
    "experience": ("경험이 드러나게", "블로거가 실제로 겪은 일이 드러나게 바꿔 주세요. 원문에 있는 경험만 씁니다."),
    "polite": ("정중하게", "정중한 존댓말(합쇼체: ~습니다)로 바꿔 주세요."),
    "friendly": ("친근하게", "친근한 해요체(~해요)로 바꿔 주세요."),
}
MAX_COMMAND = 200


class _WScene(BaseModel):
    index: int
    text: str


class RewriteDraft(BaseModel):
    scenes: list[_WScene]


class _WFix(BaseModel):
    index: int
    before: str
    after: str
    reason: str


class ReviewDraft(BaseModel):
    fixes: list[_WFix]


SYSTEM = """당신은 여행 영상 대본을 다듬는 한국어 편집자입니다. 사용자가 고른 장면의 글(자막이자 성우가 읽을 문장)만
사용자의 명령대로 다시 씁니다.

규칙:
- "고칠 장면" 목록에 있는 index만 돌려줍니다. 다른 장면은 돌려주지 않습니다.
- 원문 블로그에 없는 사실(가격, 날짜, 시간, 수치, 메뉴, 장소 이름)과 블로거가 겪지 않은 경험·감정을 지어내지 않습니다.
- 앞뒤 장면과 이야기가 자연스럽게 이어지게 하고, 앞뒤 장면과 같은 말을 되풀이하지 않습니다.
- 한 장면은 1~2문장으로, 성우가 읽기 좋게 짧게 끊습니다. 명령에 길이 지시가 없으면 원래 글자 수의 ±30% 안에 둡니다(말투를 바꿀 때도 내용을 덧붙이지 않습니다).
- 말투 지시가 없으면 원래 장면의 말투를 따릅니다."""

REVIEW_SYSTEM = """당신은 한국어 교정 편집자입니다. 여행 영상 대본(자막이자 성우가 읽는 글)에서 고칠 곳만 찾아 줍니다.

찾을 것: 맞춤법·띄어쓰기 오류, 오타, 어색한 문장, 앞뒤 장면과 맞지 않는 말(시간·장소 순서가 어긋남), 같은 표현의 반복.
규칙:
- before에는 그 장면 글에 **그대로 들어 있는** 짧은 부분(몇 낱말)을 적고, after에는 고친 글을 적습니다.
- reason은 왜 고쳐야 하는지 짧게(15자 안팎) 씁니다. 예: "띄어쓰기", "맞춤법", "앞 장면과 반복".
- 문체나 취향의 차이는 고치지 않습니다. 고칠 곳이 없으면 fixes를 빈 목록으로 둡니다.
- 사실 내용(장소 이름, 숫자)은 바꾸지 않습니다."""


def _scene_lines(script: VideoScript) -> list[dict]:
    return [{"index": i, "text": s.text, "장소": s.place} for i, s in enumerate(script.scenes)]


def _source(post: BlogPost | None, analysis_text: str) -> str:
    if post is None:
        return ""
    paras = "\n".join(p for p in post.paragraphs if p.strip())
    out = f"\n\n[원문 블로그] 제목: {post.title}\n{paras[:12000]}"
    if analysis_text:
        out += f"\n\n[여행 분석]\n{analysis_text[:4000]}"
    return out


def command_text(preset: str | None, command: str | None) -> str:
    """빠른 명령과 직접 쓴 명령을 하나로 합친다. 둘 다 없으면 빈 문자열."""
    parts = []
    if preset in PRESETS:
        parts.append(PRESETS[preset][1])
    if command and command.strip():
        parts.append(" ".join(command.split())[:MAX_COMMAND])
    return " ".join(parts)


def rewrite(script: VideoScript, indexes: list[int], command: str, api_key: str, post: BlogPost | None = None,
            analysis=None, client=None) -> list[dict]:
    """indexes 장면만 command대로 다시 쓴 제안: [{index, before, after}] (바뀐 것만)."""
    from app.ai.analyze import brief
    from app.script import ask_claude

    n = len(script.scenes)
    targets = sorted({i for i in indexes if 0 <= i < n})
    if not targets or not command.strip():
        return []
    request = ("[명령] " + command + "\n[고칠 장면] " + ", ".join(map(str, targets))
               + "\n\n[지금 대본 전체] (고칠 장면 외에는 앞뒤 흐름을 보는 참고용)\n"
               + json.dumps(_scene_lines(script), ensure_ascii=False, indent=0)
               + _source(post, brief(analysis)))
    answer = ask_claude(SYSTEM, request, RewriteDraft, api_key, client, task="rewrite",
                        accept=lambda d: any(x.index in targets and x.text.strip() for x in d.scenes))
    out, seen = [], set()
    for x in answer.scenes:
        text = " ".join(x.text.split())
        if x.index in targets and x.index not in seen and text and text != script.scenes[x.index].text:
            seen.add(x.index)
            out.append({"index": x.index, "before": script.scenes[x.index].text, "after": text})
    return sorted(out, key=lambda d: d["index"])


def review(script: VideoScript, api_key: str, client=None) -> list[dict]:
    """맞춤법·문맥 검토 제안: [{index, before, after, reason, text}] — text는 고친 뒤의 장면 글 전체."""
    from app.script import ask_claude

    if not script.scenes:
        return []
    request = "아래 대본에서 고칠 곳을 찾아 주세요.\n" + json.dumps(_scene_lines(script), ensure_ascii=False, indent=0)
    answer = ask_claude(REVIEW_SYSTEM, request, ReviewDraft, api_key, client, task="review")
    out, seen = [], set()
    for f in answer.fixes:
        if not 0 <= f.index < len(script.scenes):
            continue
        text = script.scenes[f.index].text
        before, after = f.before.strip(), " ".join(f.after.split())
        if not before or before == after or before not in text or (f.index, before) in seen:
            continue          # 대본에 없는 부분을 고치라는 제안은 버린다
        seen.add((f.index, before))
        out.append({"index": f.index, "before": before, "after": after, "reason": f.reason.strip()[:30],
                    "text": text.replace(before, after, 1)})
    return out
