"""쇼츠: 완성한 대본에서 가장 흥미로운 장면만 골라 30~60초 세로 영상 대본을 만든다.

AI(작업 script)가 첫 문장(후킹)과 장면을 고르고 글을 짧게 다듬는다. 사진은 고른 장면의 사진을 그대로 쓴다.
사용자가 확인·수정한 뒤에만 영상으로 만든다.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from app.models import BlogPost, Scene, VideoScript

MAX_CHARS = 300      # 성우가 1초에 5.5자쯤 읽으므로 약 55초
MAX_SCENES = 9


class _WShortScene(BaseModel):
    index: int       # 원래 대본의 장면 번호
    text: str        # 쇼츠에서 읽을 짧은 글


class ShortsDraft(BaseModel):
    hook: str
    hook_photo: int
    scenes: list[_WShortScene]
    title: str


SYSTEM = f"""당신은 여행 유튜브 쇼츠 편집자입니다. 완성된 여행 영상 대본에서 가장 흥미로운 부분만 골라 30~60초 세로 영상(쇼츠) 대본을 만듭니다.

- hook: 첫 1~2초에 시청자를 붙잡는 한 문장(25자 이내). 질문이나 놀라움으로. 예: "통영에서 이걸 안 먹으면 손해예요"
- hook_photo: 후킹 장면에 쓸 가장 눈길을 끄는 사진 번호.
- scenes: 원래 대본에서 고른 장면 4~8개(index는 원래 장면 번호, 시간 순서대로). text는 쇼츠용으로 짧게(30자 안팎).
  hook과 scenes의 글자 수를 모두 더해 {MAX_CHARS - 60}자 안팎(최대 {MAX_CHARS}자)이 되게 합니다.
- 마지막 장면은 짧은 마무리(예: "전체 여행은 본편에서!").
- title: 쇼츠 제목(40자 이내).
- 원래 대본·블로그에 없는 사실과 경험을 지어내지 않습니다."""


def _request(script: VideoScript, post: BlogPost | None, analysis) -> str:
    from app.ai.analyze import brief

    scenes = [{"index": i, "text": s.text, "장소": s.place, "사진": s.image_index} for i, s in enumerate(script.scenes)]
    out = "[원래 대본]\n" + json.dumps(scenes, ensure_ascii=False, indent=0)
    info = (analysis or {}).get("photos") if isinstance(analysis, dict) else None
    if info:
        out += "\n\n[사진 설명]\n" + "\n".join(
            f"사진 {p.get('index')}: [{p.get('category')}] {p.get('description')}" + (" (눈길을 끄는 사진)" if p.get("highlight") else "")
            for p in info)
    b = brief(analysis)
    if b:
        out += "\n\n[여행 분석]\n" + b[:3000]
    return out


def build(draft: ShortsDraft, script: VideoScript, n_images: int) -> VideoScript:
    """AI의 답 → 쇼츠 대본. 없는 장면 번호는 버리고, 글자 수가 넘치면 뒤 장면을 뺀다."""
    picked = []
    for x in draft.scenes:
        text = " ".join(x.text.split())
        if 0 <= x.index < len(script.scenes) and text and x.index not in [p[0] for p in picked]:
            picked.append((x.index, text))
    picked.sort()
    excluded = set(script.excluded_images)
    hook_photo = draft.hook_photo if 0 <= draft.hook_photo < n_images and draft.hook_photo not in excluded else None
    if hook_photo is None and picked:
        hook_photo = script.scenes[picked[0][0]].image_index
    hook = " ".join(draft.hook.split())[:40]
    scenes = [Scene(text=hook, image_index=hook_photo, section="opening", motion="zoom_in")] if hook else []
    total = len(hook)
    for idx, text in picked[:MAX_SCENES]:
        if total + len(text) > MAX_CHARS and len(scenes) >= 3:
            break
        src = script.scenes[idx]
        scenes.append(Scene(text=text, image_index=src.image_index, more_images=src.more_images[:2], place=src.place,
                            section=src.section, motion=src.motion, transition=src.transition, look=src.look))
        total += len(text)
    title = " ".join(draft.title.split())[:60] or script.yt_title
    return VideoScript(scenes=scenes, yt_title=(title + " #shorts")[:100], yt_description=script.yt_description,
                       yt_tags=list(dict.fromkeys([*script.yt_tags, "shorts"]))[:15],
                       intro_title=script.intro_title, excluded_images=script.excluded_images)


def plan(script: VideoScript, post: BlogPost, analysis, api_key: str, client=None) -> VideoScript:
    from app.script import ask_claude

    n = len(script.scenes)
    d = ask_claude(SYSTEM, _request(script, post, analysis), ShortsDraft, api_key, client, task="script",
                   accept=lambda x: any(0 <= s.index < n for s in x.scenes))
    return build(d, script, len(post.images))
