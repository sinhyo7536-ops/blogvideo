"""연출 요청 반영: 사용자가 글로 적은 요청(장면마다 + 영상 전체)을 AI가 읽고 영상 설정으로 바꾼다.

"영상 만들기"를 누르면 영상을 만들기 전에 한 번 실행된다. AI가 정할 수 있는 것은
사진 움직임과 세기, 장면 전환, 색감, 자막 크기·위치, 머무는 시간, (요청이 있을 때만) 자막 글,
그리고 영상 전체의 스타일·말하기 속도·배경음악 크기다.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from app.models import (CAPTION_POSITIONS, CAPTION_SIZES, LOOKS, MAX_HOLD, MOTIONS, STRENGTHS, TRANSITIONS,
                        RenderOptions, VideoScript)

TEMPLATES = ("clean", "mood", "vlog")
SPEEDS = ("slow", "normal", "fast")
KEEP = "keep"


class SceneDirection(BaseModel):
    index: int
    text: str = ""
    motion: str = KEEP
    transition: str = KEEP
    strength: str = KEEP
    look: str = KEEP
    caption_size: str = KEEP
    caption_pos: str = KEEP
    hold: float = -1.0          # -1 = 그대로


class Direction(BaseModel):
    scenes: list[SceneDirection]
    template: str = KEEP
    speed: str = KEEP
    bgm_volume: float = -1.0    # -1 = 그대로


def _enum(values) -> dict:
    return {"type": "string", "enum": [KEEP, "auto", *values]}


# Gemini가 잘 받는 단순한 형식 ($ref/$defs 없이 펼친 형태)
SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "motion": _enum(MOTIONS),
                    "transition": _enum(TRANSITIONS),
                    "strength": _enum(STRENGTHS),
                    "look": _enum(LOOKS),
                    "caption_size": _enum(CAPTION_SIZES),
                    "caption_pos": _enum(CAPTION_POSITIONS),
                    "hold": {"type": "number"},
                },
                "required": ["index", "text", "motion", "transition", "strength", "look", "caption_size",
                             "caption_pos", "hold"],
            },
        },
        "template": {"type": "string", "enum": [KEEP, *TEMPLATES]},
        "speed": {"type": "string", "enum": [KEEP, *SPEEDS]},
        "bgm_volume": {"type": "number"},
    },
    "required": ["scenes", "template", "speed", "bgm_volume"],
}

SYSTEM = f"""당신은 여행 영상의 연출 감독입니다. 사용자가 장면마다 적은 "연출 요청"과 영상 전체에 대한 요청을 읽고,
프로그램이 이해하는 설정 값으로 바꿉니다. 모든 장면에 대해 하나씩, index를 그대로 돌려줍니다.

정할 수 있는 값 (바꾸지 않을 값은 "{KEEP}", 프로그램 기본값으로 되돌릴 때는 "auto"):
- motion (사진 움직임): zoom_in(천천히 확대), zoom_out(천천히 축소), pan_left / pan_right(옆으로 이동), pan_up / pan_down(위아래 이동), still(움직임 없음)
- strength (움직임 세기): subtle(아주 은은하게), strong(크게·역동적으로)
- transition (앞 장면에서 이 장면으로 넘어올 때): fade(부드럽게 겹치기), slide(옆으로 밀기), push(부드럽게 밀기), circle(원으로 열기), black(어두워졌다 밝아지기), zoom(확대하며 넘기기), wipe(닦아내듯 넘기기)
- look (색감): warm(따뜻하게), cool(시원하게·푸르게), bw(흑백), vivid(선명하고 진하게), soft(은은하고 부드럽게)
- caption_size (자막 크기): small, large
- caption_pos (자막 위치): top, center, bottom
- hold (목소리가 끝난 뒤 더 머무는 시간, 초): 0 ~ {MAX_HOLD:g}. 바꾸지 않으면 -1
- text (자막이자 성우가 읽는 글)
영상 전체: template(clean=깔끔 / mood=감성 / vlog=브이로그), speed(성우 말하기 속도 slow / normal / fast), bgm_volume(배경음악 크기 0~1, 보통 0.15. 바꾸지 않으면 -1)

규칙:
- 요청에 나온 것만 바꿉니다. 요청과 상관없는 값은 "{KEEP}"(hold와 bgm_volume은 -1)로 둡니다.
- 장면의 요청이 영상 전체 요청과 다르면 장면의 요청이 우선입니다. 영상 전체 요청은 요청이 없는 장면에도 적용합니다.
- 요청에 "(이미 반영됨)"이라고 적힌 장면은 이미 처리된 것이니 모든 값을 "{KEEP}"로 두고 text도 그대로 돌려줍니다.
- text는 그대로 돌려주는 것이 기본입니다. "더 짧게", "재밌게", "존댓말로", "이 내용을 넣어 줘"처럼 글을 바꿔 달라는 요청이 있을 때만 그 장면의 글을 고칩니다. 고칠 때도 원문에 없는 사실(가격, 날짜, 장소 이름 등)을 지어내지 않고, 자연스러운 구어체(해요체)를 유지합니다.
- "느리게", "여유 있게", "오래 보여 줘" 같은 요청은 hold를 늘리고(1~3초), 필요하면 strength를 subtle로 합니다. "빠르게", "경쾌하게"는 hold 0, 전환은 slide나 push가 어울립니다.
- "감성적으로", "잔잔하게"는 look=soft 또는 warm, transition=fade, strength=subtle이 어울립니다. "강조", "임팩트"는 zoom_in + strong, caption_size=large가 어울립니다.
- 프로그램이 할 수 없는 요청(동영상 삽입, 스티커, 음악 바꾸기 등)은 무시하고 가장 가까운 설정만 정합니다."""


def needs_direction(script: VideoScript | None) -> bool:
    """아직 반영하지 않은 연출 요청이 있는지."""
    if script is None:
        return False
    if script.direction.strip() and script.direction.strip() != script.direction_done.strip():
        return True
    return any(s.direction.strip() and s.direction.strip() != s.direction_done.strip() for s in script.scenes)


def build_request(script: VideoScript, options: RenderOptions) -> str:
    overall_new = script.direction.strip() != script.direction_done.strip()
    scenes = []
    for i, s in enumerate(script.scenes):
        want = s.direction.strip()
        if want and want == s.direction_done.strip() and not overall_new:
            want += " (이미 반영됨)"
        scenes.append({
            "index": i, "text": s.text, "요청": want, "사진 있음": s.image_index is not None,
            "지금 설정": {"motion": s.motion, "transition": s.transition, "strength": s.strength, "look": s.look,
                       "caption_size": s.caption_size, "caption_pos": s.caption_pos, "hold": s.hold},
        })
    data = {
        "영상 전체 요청": script.direction.strip(),
        "지금 영상 설정": {"template": options.template, "speed": options.speed, "bgm_volume": options.bgm_volume},
        "장면": scenes,
    }
    return ("아래 영상의 연출 요청을 설정 값으로 바꿔 주세요. 장면 수는 " f"{len(scenes)}개이고, 모든 장면을 돌려줘야 합니다.\n\n"
            + json.dumps(data, ensure_ascii=False, indent=1))


def _pick(value: str, allowed, current: str) -> str:
    if value == KEEP:
        return current
    return value if value in allowed else ("auto" if value == "auto" else current)


def apply(script: VideoScript, options: RenderOptions, d: Direction) -> tuple[VideoScript, RenderOptions]:
    """AI의 답을 대본·설정에 반영한다. 모르는 값·빠진 장면은 그대로 둔다."""
    overall = script.direction.strip()
    by_index = {x.index: x for x in d.scenes}
    scenes = []
    for i, s in enumerate(script.scenes):
        x = by_index.get(i)
        want = s.direction.strip()
        if x is None:
            scenes.append(s)
            continue
        change = {
            "motion": _pick(x.motion, MOTIONS, s.motion),
            "transition": _pick(x.transition, TRANSITIONS, s.transition),
            "strength": _pick(x.strength, STRENGTHS, s.strength),
            "look": _pick(x.look, LOOKS, s.look),
            "caption_size": _pick(x.caption_size, CAPTION_SIZES, s.caption_size),
            "caption_pos": _pick(x.caption_pos, CAPTION_POSITIONS, s.caption_pos),
            "direction_done": want,
        }
        if x.hold >= 0:
            change["hold"] = min(MAX_HOLD, x.hold)
        text = " ".join((x.text or "").split())
        if text and (want or overall):   # 요청이 없는 장면의 글은 절대 바꾸지 않는다
            change["text"] = text
        scenes.append(s.model_copy(update=change))
    new_options = options
    update = {}
    if d.template in TEMPLATES:
        update["template"] = d.template
    if d.speed in SPEEDS:
        update["speed"] = d.speed
    if 0 <= d.bgm_volume <= 1:
        update["bgm_volume"] = round(d.bgm_volume, 2)
    if update:
        new_options = options.model_copy(update=update)
    return script.model_copy(update={"scenes": scenes, "direction_done": overall}), new_options


def direct(script: VideoScript, options: RenderOptions, engine: str, api_key: str,
           client=None) -> tuple[VideoScript, RenderOptions]:
    """engine: "api"(Claude) 또는 "gemini"."""
    request = build_request(script, options)
    n = len(script.scenes)

    def accept(d: Direction) -> bool:
        return len({x.index for x in d.scenes if 0 <= x.index < n}) >= max(1, n // 2)

    if engine == "gemini":
        from app.gemini import ask_gemini

        answer = ask_gemini(SYSTEM, request, SCHEMA, Direction, api_key, client, accept=accept)
    else:
        from app.script import ask_claude

        answer = ask_claude(SYSTEM, request, Direction, api_key, client, accept=accept)
    return apply(script, options, answer)
