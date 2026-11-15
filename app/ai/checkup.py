"""영상 검수: 만든 영상의 대본·사진·시간을 점검해 고칠 곳을 알려 준다.

프로그램이 직접 보는 것(사진 반복·빠진 사진·자막 길이와 읽는 속도·장면 시간·글과 사진 점수)과
AI(작업 review)가 보는 것(오탈자·문맥)을 합친다. 영상은 이 대본으로 만들어지므로 대본을 보면 영상의 문제를 찾을 수 있다.
"""
from __future__ import annotations

from app.models import BlogPost, RenderOptions, VideoScript

LONG_CAPTION = 60        # 자: 두 줄을 넘기 쉬움
FAST_READ = 7.5          # 초당 글자: 자막이 너무 빨리 지나감
SHORT_SCENE = 1.6        # 초
LONG_PHOTO = 12.0        # 초: 사진 한 장이 너무 오래 보임
LOW_SCORE = 50


def local_checks(script: VideoScript, post: BlogPost | None, options: RenderOptions) -> list[dict]:
    items: list[dict] = []
    n_img = len(post.images) if post else 0
    excluded = set(script.excluded_images)
    last_seen: dict[int, int] = {}

    def add(level, i, text):
        items.append({"level": level, "scene": i, "text": text})

    for i, s in enumerate(script.scenes):
        photos = s.photo_list()
        if not photos or all(not 0 <= p < n_img for p in photos):
            add("warn", i, "사진이 없어 어두운 화면에 자막만 나와요.")
        for p in photos:
            if p in excluded:
                add("warn", i, f"뺀 사진(사진 {p + 1}번)을 쓰고 있어요.")
            if p in last_seen and i - last_seen[p] > 1:
                add("info", i, f"사진 {p + 1}번을 장면 {last_seen[p] + 1}에서도 썼어요. 같은 사진이 다시 나와요.")
            last_seen[p] = i
        if len(s.text) > LONG_CAPTION:
            add("info", i, f"자막이 {len(s.text)}자로 길어요. 나누면 읽기 쉬워요.")
        if s.duration:
            sec = s.duration + s.hold
            if len(s.text) / max(sec, 0.1) > FAST_READ:
                add("info", i, "자막이 빨리 지나가요. 글을 줄이거나 '더 머무는 시간'을 늘려 보세요.")
            if sec < SHORT_SCENE:
                add("info", i, f"장면이 {sec:.1f}초로 짧아요.")
            if len(photos) <= 1 and sec > LONG_PHOTO:
                add("info", i, f"사진 한 장이 {sec:.0f}초 동안 보여요. 사진을 더하거나 장면을 나눠 보세요.")
        if 0 <= s.photo_score < LOW_SCORE:
            add("warn", i, f"글과 사진이 잘 안 맞을 수 있어요 ({s.photo_score}점).")
    if not script.yt_title.strip():
        add("warn", -1, "유튜브 제목이 비어 있어요.")
    return items


def checkup(script: VideoScript, post: BlogPost | None, options: RenderOptions, api_key: str = "",
            review=None) -> dict:
    """review: (대본, 키) → 맞춤법·문맥 제안 (app.ai.rewrite.review). 키가 없으면 프로그램 점검만."""
    items = local_checks(script, post, options)
    fixes = review(script, api_key) if (review and api_key) else []
    for f in fixes:
        items.append({"level": "typo", "scene": f["index"], "text": f"'{f['before']}' → '{f['after']}' ({f['reason']})"})
    items.sort(key=lambda x: (x["scene"], {"warn": 0, "typo": 1, "info": 2}[x["level"]]))
    return {"items": items, "fixes": fixes,
            "counts": {k: sum(1 for x in items if x["level"] == k) for k in ("warn", "typo", "info")}}
