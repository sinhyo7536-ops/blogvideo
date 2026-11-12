"""AI 없이 블로그 글을 장면 대본으로 나누기 (무료)."""
from __future__ import annotations

import re

from app.models import BlogPost, Scene, VideoScript

MAX_SCENES = 12
MAX_LEN = 60
CLOSING = "끝까지 봐 주셔서 감사합니다. 구독과 좋아요 부탁드려요!"
_STOP = {"그리고", "정말", "너무", "오늘", "후기", "솔직", "있는", "없는", "하는", "합니다", "입니다"}


def _sentences(paragraphs: list[str]) -> list[str]:
    out: list[str] = []
    for p in paragraphs:
        for s in re.split(r"(?<=[.!?。])\s+", p.strip()):
            s = s.strip()
            if len(s) >= 4:
                out.extend(_chunk(s))
    return out


def _chunk(sentence: str) -> list[str]:
    """긴 문장은 쉼표·공백 기준으로 MAX_LEN 이하 조각으로 나눈다."""
    if len(sentence) <= MAX_LEN:
        return [sentence]
    parts: list[str] = []
    cur = ""
    for word in re.split(r"(?<=,)\s*|\s+", sentence):
        if not word:
            continue
        cand = f"{cur} {word}".strip()
        if len(cand) <= MAX_LEN:
            cur = cand
        else:
            if cur:
                parts.append(cur)
            cur = word[:MAX_LEN]
    if cur:
        parts.append(cur)
    return parts


def _pick(items: list[str], n: int) -> list[str]:
    """글 전체에서 고르게 n개를 고른다."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _tags(title: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", title)
    tags: list[str] = []
    for w in words:
        if w not in _STOP and w not in tags:
            tags.append(w)
    return tags[:8] or ["블로그"]


def auto_script(post: BlogPost) -> VideoScript:
    body = _pick(_sentences(post.paragraphs), MAX_SCENES - 2)
    title = post.title.strip() or "오늘의 이야기"
    texts = [title[:MAX_LEN]] + body + [CLOSING]
    n_img = len(post.images)
    scenes = []
    for i, text in enumerate(texts):
        idx = None if n_img == 0 else min(n_img - 1, int(i * n_img / len(texts)))
        scenes.append(Scene(text=text, image_index=idx))
    first = " ".join(post.paragraphs[:2])
    desc = (first[:200] + ("…" if len(first) > 200 else "")).strip()
    return VideoScript(scenes=scenes, yt_title=title[:60],
                       yt_description=(desc + "\n\n" if desc else "") + f"원문: {post.url}",
                       yt_tags=_tags(title))
