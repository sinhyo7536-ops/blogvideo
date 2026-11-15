"""블로그 텍스트 그대로 가져오기: 블로그 문단을 순서대로 장면으로 만들고, 블로그에서 그 문단 바로 뒤에 있던
사진을 그 장면에 붙인다. 성우는 블로그 글을 그대로 읽는다. AI를 쓰지 않는다(2026-11-15 사용자 결정).

자막·음성·사진이 블로거가 배치한 그대로 맞물리게 하는 것이 목적이다.
"""
from __future__ import annotations

import re

from app.models import MAX_MORE_IMAGES, BlogPost, Scene, VideoScript

MAX_CAPTION = 60     # 자막 한 장면의 글자 수 (두 줄 안팎)
JOIN_UNDER = 30      # 아주 짧은 줄("출발!")은 이 길이까지 한 장면으로 합친다 (줄마다 사진을 맞추려고 짧게)
PER_SCENE = MAX_MORE_IMAGES + 1

_BOUNDARY = re.compile(r"^\[\d+번째 글\]")
_URL = re.compile(r"^(https?://|www\.)\S+$")
_SENTENCE = re.compile(r"(?<=[.!?。…~])\s+|(?<=[요다죠네까])\s+(?=[가-힣A-Z0-9\"'(])")


def clean_paragraph(p: str) -> str:
    """읽을 글만 남긴다. 해시태그만 있는 줄·주소·기호뿐인 줄·여러 글 경계 표시는 빈 문자열."""
    t = " ".join(p.split())
    if not t or _BOUNDARY.match(t) or _URL.match(t):
        return ""
    words = t.split()
    if len(words) >= 2 and all(w.startswith("#") for w in words):
        return ""
    t = re.sub(r"(^|\s)#(?=\S)", r"\1", t)        # "#남파랑길" → "남파랑길" (성우가 '샵'이라고 읽지 않게)
    if not re.search(r"[가-힣A-Za-z0-9ㄱ-ㅎ]", t):
        return ""
    return t.strip()


def _cut_long(sentence: str, limit: int) -> list[str]:
    """한 문장이 너무 길면 고르게 나눈다: 몇 조각이 필요한지 정하고, 조각마다 목표 길이에 가장 가까운
    쉼표·띄어쓰기 자리에서 자른다 (끝에 짧은 꼬리가 남지 않게)."""
    out = []
    rest = sentence
    while len(rest) > limit:
        pieces = -(-len(rest) // limit)
        target = len(rest) // pieces
        spaces = [i for i, ch in enumerate(rest[:limit + 1]) if ch == " "]
        if spaces:
            cut = min(spaces, key=lambda i: (abs(i - target) - (3 if rest[i - 1:i] == "," else 0)))
        else:
            cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def split_caption(text: str, limit: int = MAX_CAPTION) -> list[str]:
    """문장을 묶어 자막 한 장면 길이(limit) 안으로 나눈다."""
    parts: list[str] = []
    for sent in (s.strip() for s in _SENTENCE.split(text) if s.strip()):
        for piece in _cut_long(sent, limit):
            if parts and len(parts[-1]) + 1 + len(piece) <= limit:
                parts[-1] += " " + piece
            else:
                parts.append(piece)
    return parts


def _spread(photos: list[int], n: int) -> list[list[int] | None]:
    """한 덩어리(사진 앞의 글 n장면)에 사진을 나눈다. 사진이 장면만큼 있으면 순서대로 고르게.
    사진이 적으면 사진에 가까운 뒤쪽 장면들이 나눠 쓰고(사진 한 장당 두 장면까지), 앞쪽 장면은 None
    (= 바로 앞에 보이던 사진을 이어서)."""
    if not photos:
        return [None] * n
    m = len(photos)
    if m >= n:
        return [photos[-(-j * m // n):-(-(j + 1) * m // n)] for j in range(n)]   # 남는 사진은 앞 장면부터
    covered = min(n, 2 * m)
    head = [None] * (n - covered)
    return head + [[photos[j * m // covered]] for j in range(covered)]


def text_script(post: BlogPost) -> VideoScript:
    """블로그는 보통 '글 몇 줄 → 그 글의 사진들' 순서다. 사진이 나올 때까지의 글을 한 덩어리로 모아,
    그 뒤의 사진을 덩어리 안 장면들에 순서대로 나눠 붙인다."""
    from app.autoscript import _tags
    from app.script import short_title

    after: dict[int, list[int]] = {}
    for i, im in enumerate(post.images):
        after.setdefault(im.para if im.para >= 0 else -1, []).append(i)
    lead = list(after.get(-1, []))               # 첫 문단보다 앞의 사진 → 첫 장면 앞쪽에
    scenes: list[dict] = []                       # {"text", "photos": list | None}
    block: list[str] = []                         # 아직 사진을 못 만난 글 (자막 조각)
    overflow: list[int] = []

    def flush(photos: list[int]) -> None:
        nonlocal block, lead
        photos = lead + photos
        lead = []
        if not block:
            if not photos:
                return
            if scenes:                            # 글 없이 사진만 이어지면 앞 장면에 더한다
                last = scenes[-1]
                last["photos"] = (last["photos"] or []) + photos
            else:
                lead = photos
            return
        for part, group in zip(block, _spread(photos, len(block))):
            scenes.append({"text": part, "photos": group})
        block = []

    for k, raw in enumerate(post.paragraphs):
        text = clean_paragraph(raw)
        if text:
            if block and len(block[-1]) + 1 + len(text) <= JOIN_UNDER:
                block[-1] += " " + text           # 사진 사이의 짧은 줄끼리는 한 장면으로
            else:
                block.extend(split_caption(text))
        photos = after.get(k, [])
        if photos:
            flush(photos)
    flush([])
    if lead:                                      # 글이 하나도 없으면 사진은 쓸 곳이 없다
        overflow.extend(lead)

    first = next((s["photos"][0] for s in scenes if s["photos"]), None)
    shown = None
    out = []
    for s in scenes:
        photos = s["photos"] or []
        if len(photos) > PER_SCENE:               # 한 장면에 다 못 넣는 사진은 뒤에서 빈자리를 찾는다
            overflow.extend(photos[PER_SCENE:])
            photos = photos[:PER_SCENE]
        if photos:
            shown = photos[-1]
            out.append(Scene(text=s["text"], image_index=photos[0], more_images=photos[1:]))
        else:   # 자기 사진이 없는 장면: 바로 앞에 보이던 사진(처음이면 첫 사진)을 이어서 보여 준다
            out.append(Scene(text=s["text"], image_index=shown if shown is not None else first))
    for p in overflow:
        for j in range(len(out) - 1, -1, -1):
            if p in out[j].photo_list():
                break
            if len(out[j].photo_list()) < PER_SCENE:
                if out[j].image_index is None:
                    out[j] = out[j].model_copy(update={"image_index": p})
                else:
                    out[j] = out[j].model_copy(update={"more_images": out[j].more_images + [p]})
                break
    title = post.title.strip()
    return VideoScript(scenes=out, yt_title=title[:60] or "여행 영상",
                       yt_description=f"{title}\n\n원문: {post.url}".strip(), yt_tags=_tags(title),
                       intro_title=short_title(title) if title else "")
