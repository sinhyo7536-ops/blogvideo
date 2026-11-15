"""사진 매칭: 장면 글과 사진이 얼마나 맞는지 점수를 매기고, 더 맞는 사진을 추천한다.

사진을 다시 보내지 않고, 여행 분석(app.ai.analyze)이 사진을 보고 적어 둔 설명·분류·장소·위치를 쓴다
(사진 30장을 다시 보내는 것보다 훨씬 싸고 빠르다). 결과는 제안이며, 적용은 사용자가 고른다.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from app.models import BlogPost, VideoScript

LOW_SCORE = 50   # 이보다 낮으면 화면에 '글과 사진이 잘 안 맞을 수 있어요'


class _WMatch(BaseModel):
    index: int
    current_score: int     # 지금 고른 사진이 글과 맞는 정도 0~100 (사진이 없으면 0)
    photos: list[int]      # 추천 사진 (좋은 순, 1~3장)
    score: int             # 첫 추천 사진이 글과 맞는 정도 0~100
    reason: str


class MatchDraft(BaseModel):
    scenes: list[_WMatch]


SYSTEM = """당신은 여행 영상 편집자입니다. 장면마다 자막 글과 가장 잘 맞는 사진을 고릅니다.
사진은 직접 보지 않고, 미리 사진을 보고 적어 둔 설명(분류, 보이는 것, 장소, 블로그에서 몇째 문단 뒤에 있었는지)으로 판단합니다.

규칙:
- 모든 장면에 대해 하나씩 돌려줍니다(index 그대로).
- current_score: 지금 그 장면에 붙은 사진이 글과 맞는 정도(0~100). 사진이 없으면 0.
- photos: 그 장면에 가장 잘 맞는 사진 번호를 좋은 순서로 1~3개. score: 첫 번째 사진이 맞는 정도(0~100).
- 점수 기준: 글이 말하는 장소·음식·장면이 사진에 그대로 보이면 80 이상, 같은 장소의 다른 모습이면 50~79,
  관련이 약하면 50 미만.
- 글의 흐름과 사진 순서가 크게 어긋나지 않게 하고, 같은 사진을 멀리 떨어진 여러 장면에 쓰지 않습니다(바로 이어지는 장면은 괜찮음).
- "쓰지 않을 사진"에 있는 번호는 절대 고르지 않습니다.
- reason은 왜 그 사진인지 20자 안팎으로 씁니다. 예: "회 사진, 글과 같은 식당"."""


def photo_table(post: BlogPost, analysis: dict | None) -> list[str]:
    info = {p.get("index"): p for p in ((analysis or {}).get("photos") or [])}
    lines = []
    for i, im in enumerate(post.images):
        p = info.get(i, {})
        where = f"문단 {im.para} 뒤" if im.para >= 0 else "첫 문단 앞"
        desc = p.get("description") or "(설명 없음)"
        lines.append(f"사진 {i}: [{p.get('category') or '기타'}] {desc} / 장소 {p.get('place') or '?'} / {where}"
                     + (" / 눈길을 끄는 사진" if p.get("highlight") else ""))
    return lines


def _request(script: VideoScript, post: BlogPost, analysis: dict | None) -> str:
    excluded = sorted(set(script.excluded_images))
    scenes = [{"index": i, "text": s.text, "장소": s.place, "지금 사진": s.image_index}
              for i, s in enumerate(script.scenes)]
    return ("[사진 목록]\n" + "\n".join(photo_table(post, analysis))
            + f"\n\n[쓰지 않을 사진] {', '.join(map(str, excluded)) or '없음'}"
            + "\n\n[장면]\n" + json.dumps(scenes, ensure_ascii=False, indent=0))


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def spread(matches: list[dict]) -> list[dict]:
    """같은 사진이 멀리 떨어진 장면에 거듭 쓰이면, 그 장면의 다른 추천 사진으로 바꾼다."""
    used: dict[int, int] = {}
    for m in matches:
        best = m["best"]
        if best is not None and best in used and m["index"] - used[best] > 1:
            other = next((x for x in m["alts"] if x not in used), None)
            if other is not None:
                m["alts"] = [other] + [x for x in m["alts"] if x != other]
                m["best"] = other
                m["score"] = max(0, m["score"] - 10)   # 두 번째 추천이라 조금 낮춰 둔다
        if m["best"] is not None:
            used[m["best"]] = m["index"]
    return matches


def match(script: VideoScript, post: BlogPost, analysis: dict | None, api_key: str, client=None) -> list[dict]:
    """장면마다 {index, current, current_score, best, score, alts, reason}."""
    from app.script import ask_claude

    n, n_img = len(script.scenes), len(post.images)
    if not n or not n_img:
        return []
    banned = set(script.excluded_images)
    answer = ask_claude(SYSTEM, _request(script, post, analysis), MatchDraft, api_key, client, task="photos",
                        accept=lambda d: len({x.index for x in d.scenes if 0 <= x.index < n}) >= max(1, n // 2))
    by_index = {x.index: x for x in answer.scenes if 0 <= x.index < n}
    out = []
    for i, s in enumerate(script.scenes):
        x = by_index.get(i)
        if x is None:
            continue
        alts = []
        for p in x.photos:
            if 0 <= p < n_img and p not in banned and p not in alts:
                alts.append(p)
        out.append({"index": i, "current": s.image_index, "current_score": _clamp(x.current_score),
                    "best": alts[0] if alts else None, "score": _clamp(x.score) if alts else 0,
                    "alts": alts[:3], "reason": " ".join(x.reason.split())[:40]})
    return spread(out)
