"""유튜브에 올릴 글: 제목 후보, 설명(+ 장면 순서로 만든 챕터), 해시태그, 썸네일 문구, 검색 키워드.

AI(작업 metadata)는 글을 쓰고, 챕터 시간은 프로그램이 영상 길이로 직접 계산한다(AI가 시간을 지어내지 않게).
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from app.models import SECTION_LABELS, BlogPost, RenderOptions, VideoScript


class YoutubeDraft(BaseModel):
    titles: list[str]
    description: str
    hashtags: list[str]
    thumbnail_texts: list[str]
    keywords: list[str]


SYSTEM = """당신은 한국 여행 유튜브 채널의 업로드 담당자입니다. 완성된 여행 영상의 대본을 보고 유튜브에 올릴 글을 씁니다.

- titles: 제목 후보 5개. 60자 이내. 정보형(어디·무엇), 감성형, 궁금증형 등 느낌을 서로 다르게. 낚시성 과장 금지.
- description: 영상 소개 3~5줄 + 빈 줄 + "방문한 곳" 목록(대본에 나온 장소만, 줄마다 "· 장소 이름"). 챕터·원문 링크는 쓰지 않습니다(프로그램이 붙임).
- hashtags: 8~12개, # 없이 낱말만(띄어쓰기 없이). 지역·장소·여행 종류 위주.
- thumbnail_texts: 썸네일에 크게 넣을 문구 3개. 12자 안팎, 한눈에 읽히게.
- keywords: 검색 키워드 10개 (예: "통영 1박2일 코스").
- 대본·원문에 없는 사실(가격, 시간, 순위, 장소 이름)을 지어내지 않습니다."""


def _clean_list(items: list[str], limit: int, size: int, strip_hash: bool = False) -> list[str]:
    out = []
    for x in items:
        x = " ".join(str(x).split())
        if strip_hash:
            x = x.lstrip("#").replace(" ", "")
        x = x[:size]
        if x and x not in out:
            out.append(x)
    return out[:limit]


def _clock(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def chapters(script: VideoScript, options: RenderOptions) -> list[tuple[float, str]]:
    """장소(없으면 대본 구성)가 바뀌는 곳마다 챕터. 유튜브 규칙: 0:00에서 시작, 3개 이상, 각 10초 이상."""
    from app import visuals
    from app.render import INTRO_SEC, photo_shares

    if not script.scenes or any(not s.duration for s in script.scenes):
        return []
    tpl = visuals.template(options.template)
    title = (script.intro_title or script.yt_title).strip()
    t = INTRO_SEC if options.intro and title else 0.0
    marks: list[tuple[float, str]] = [(0.0, "시작")] if t else []
    for s in script.scenes:
        label = s.place.strip() or SECTION_LABELS.get(s.section, "")
        if label and (not marks or marks[-1][1] != label):
            marks.append((t, label))
        base = s.duration + s.hold + tpl.extra_hold
        n = len(s.photo_list())
        t += photo_shares(base, n, s.photo_secs)[1] if n > 1 else base
    out: list[tuple[float, str]] = []
    for at, label in marks:
        if out and at - out[-1][0] < 10:      # 10초가 안 되는 챕터는 앞 챕터에 합친다
            if out[-1][1] == "시작":           # 시작 화면 바로 뒤의 첫 장소는 그 이름을 0:00 챕터로
                out[-1] = (out[-1][0], label)
            continue
        out.append((at, label))
    if out:
        out[0] = (0.0, out[0][1])
    return out if len(out) >= 3 else []


def chapter_text(marks: list[tuple[float, str]]) -> str:
    return "\n".join(f"{_clock(at)} {label}" for at, label in marks)


def _request(script: VideoScript, post: BlogPost | None, analysis) -> str:
    from app.ai.analyze import brief

    lines = [s.text for s in script.scenes]
    places = []
    for s in script.scenes:
        if s.place and s.place not in places:
            places.append(s.place)
    out = (f"[영상 제목(지금)] {script.yt_title}\n[여행 제목] {script.intro_title}\n[장소] {', '.join(places) or '없음'}"
           f"\n\n[대본]\n" + "\n".join(lines))
    if post is not None:
        out += f"\n\n[원문 블로그 제목] {post.title}"
    b = brief(analysis)
    if b:
        out += "\n\n[여행 분석]\n" + b[:3000]
    return out


def generate(script: VideoScript, post: BlogPost | None, analysis, options: RenderOptions, api_key: str,
             client=None) -> dict:
    from app.script import ask_claude

    d = ask_claude(SYSTEM, _request(script, post, analysis), YoutubeDraft, api_key, client, task="metadata",
                   accept=lambda x: bool(x.titles and x.description.strip()))
    marks = chapters(script, options)
    desc = d.description.strip()
    if marks:
        desc += "\n\n챕터\n" + chapter_text(marks)
    if post is not None:
        desc += f"\n\n원문: {post.url}"
    tags = _clean_list(d.hashtags, 12, 30, strip_hash=True)
    return {"titles": _clean_list(d.titles, 5, 100), "description": desc, "hashtags": tags,
            "hashtag_text": " ".join("#" + t for t in tags),
            "thumbnail_texts": _clean_list(d.thumbnail_texts, 3, 20), "keywords": _clean_list(d.keywords, 10, 40),
            "chapters": [{"at": round(at, 1), "label": label} for at, label in marks]}
