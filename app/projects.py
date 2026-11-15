"""프로젝트: 여러 블로그 글을 하나의 영상 재료로 합치기, 중복 글 찾기, 목록에 보일 요약.

프로젝트는 따로 저장하지 않고 작업(JobState) 하나가 프로젝트 하나다(이미 저장·이어 하기가 된다).
"""
from __future__ import annotations

import hashlib

from app.models import BlogPost, JobState

MAX_URLS = 10
DUP_POST = "앞에 넣은 글과 같은 글이라 뺐어요."


def post_fingerprint(post: BlogPost) -> str:
    """같은 글인지 (주소가 달라도: 모바일/PC 주소, 단축 링크). 제목과 앞부분 본문으로 본다."""
    head = " ".join(" ".join(post.paragraphs[:5]).split())[:600]
    key = " ".join(post.title.split()) + "\n" + head
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def heading(number: int, title: str) -> str:
    return f"[{number}번째 글] {title}"


def append_post(base: BlogPost | None, post: BlogPost, number: int, multi: bool) -> BlogPost:
    """post 를 base 뒤에 붙인다. 여러 글이면 글마다 '[n번째 글] 제목' 문단을 넣어 경계를 표시하고,
    사진의 문단 번호(para)와 글 번호(post)를 합친 글 기준으로 고친다."""
    paras = list(base.paragraphs) if base else []
    if multi:
        paras.append(heading(number, post.title))
    offset = len(paras)
    images = [im.model_copy(update={"para": (offset + im.para) if im.para >= 0 else offset - 1,
                                    "post": number - 1})
              for im in post.images]
    missing = [f"{number}번째 글의 {m}" if multi else m for m in post.missing]
    if base is None:
        title = post.title
        return post.model_copy(update={"paragraphs": paras + list(post.paragraphs), "images": images,
                                       "missing": missing, "title": title})
    return base.model_copy(update={
        "paragraphs": paras + list(post.paragraphs),
        "images": list(base.images) + images,
        "missing": list(base.missing) + missing,
        "duplicates": base.duplicates + post.duplicates,
        "date": base.date or post.date,
        "blog_name": base.blog_name or post.blog_name,
    })


def merged_title(titles: list[str]) -> str:
    titles = [t for t in titles if t.strip()]
    if not titles:
        return ""
    return titles[0] if len(titles) == 1 else f"{titles[0]} 외 {len(titles) - 1}편"


def source(url: str, status: str, post: BlogPost | None = None, message: str = "") -> dict:
    """글 하나를 불러온 결과 (화면의 '불러온 글' 목록)."""
    d = {"url": url, "status": status, "message": message, "title": "", "paragraphs": 0, "photos": 0,
         "missing": 0, "duplicates": 0}
    if post is not None:
        d.update(title=post.title, paragraphs=len(post.paragraphs), photos=len(post.images),
                 missing=len(post.missing), duplicates=post.duplicates)
    return d


def summary(s: JobState) -> dict:
    """프로젝트 목록 한 줄."""
    scenes = len(s.script.scenes) if s.script else 0
    return {"id": s.id, "name": s.name or (s.post.title if s.post else s.url), "stage": s.stage,
            "created_at": s.created_at, "finished_at": s.finished_at, "scenes": scenes,
            "posts": len(s.urls) or 1, "has_video": bool(s.video_path), "message": s.message,
            "photos": len(s.post.images) if s.post else 0}
