"""블로그 제목에서 유튜브 태그 뽑기 (직접 작성용 빈 대본에 쓴다).

예전에는 여기서 "AI 없이" 문장을 장면으로 나누는 대본도 만들었지만, 그 방식은 없앴다(2026-11-15 사용자 결정:
대본은 Claude만).
"""
from __future__ import annotations

import re

_STOP = {"그리고", "정말", "너무", "오늘", "후기", "솔직", "있는", "없는", "하는", "합니다", "입니다"}


def _tags(title: str) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", title)
    tags: list[str] = []
    for w in words:
        if w not in _STOP and w not in tags:
            tags.append(w)
    return tags[:8] or ["블로그"]
