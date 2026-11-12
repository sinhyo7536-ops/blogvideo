"""네이버 블로그 글 추출."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.errors import UserError
from app.models import BlogPost, ImageRef

UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")
REFERER = "https://m.blog.naver.com/"
BAD_URL = "네이버 블로그 글 주소를 넣어 주세요."
UNREADABLE = "글을 읽을 수 없어요. 전체공개 글인지 확인해 주세요."
OFFLINE = "인터넷 연결을 확인해 주세요."

_HOSTS = {"blog.naver.com", "m.blog.naver.com"}
_SHORT_HOSTS = {"naver.me"}
_INVISIBLE = re.compile(r"[​‌‍﻿]")


def _normalize(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def parse_naver_url(url: str) -> tuple[str, str]:
    u = urlparse(_normalize(url))
    if u.hostname not in _HOSTS:
        raise UserError(BAD_URL)
    if u.path.lower().endswith("postview.naver") or u.path.lower().endswith("postview.nhn"):
        q = parse_qs(u.query)
        blog_id, log_no = q.get("blogId", [""])[0], q.get("logNo", [""])[0]
    else:
        parts = [p for p in u.path.split("/") if p]
        blog_id, log_no = (parts + ["", ""])[:2]
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", blog_id or "") or not re.fullmatch(r"\d+", log_no or ""):
        raise UserError(BAD_URL)
    return blog_id, log_no


def resolve_url(url: str, client: httpx.Client) -> str:
    url = _normalize(url)
    if urlparse(url).hostname not in _SHORT_HOSTS:
        return url
    try:
        r = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        raise UserError(OFFLINE) from e
    return str(r.url)


def _hires(url: str) -> str:
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query) if k != "type"] + [("type", "w966")]
    return urlunparse(u._replace(query=urlencode(q)))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", text)).strip()


def extract_post(html: str, url: str) -> BlogPost:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(".se-main-container")
    if container is None:
        raise UserError(UNREADABLE)
    title_el = soup.select_one(".se-title-text")
    og = soup.find("meta", property="og:title")
    title = _clean(title_el.get_text(" ")) if title_el else _clean(og["content"]) if og else ""
    paragraphs = [t for p in container.select(".se-text-paragraph") if (t := _clean(p.get_text(" ")))]
    images = []
    for img in container.select("img.se-image-resource"):
        src = img.get("data-lazy-src") or img.get("src") or ""
        if src.startswith("http"):
            images.append(ImageRef(url=_hires(src)))
    if not paragraphs and not images:
        raise UserError(UNREADABLE)
    return BlogPost(title=title, paragraphs=paragraphs, images=images, url=url)


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True)


def fetch_post(url: str) -> BlogPost:
    with _client() as c:
        final = resolve_url(url, c)
        blog_id, log_no = parse_naver_url(final)
        try:
            r = c.get(f"https://m.blog.naver.com/{blog_id}/{log_no}")
        except httpx.HTTPError as e:
            raise UserError(OFFLINE) from e
        if r.status_code != 200:
            raise UserError(UNREADABLE)
        return extract_post(r.text, f"https://blog.naver.com/{blog_id}/{log_no}")


def download_images(post: BlogPost, dest: Path, client: httpx.Client | None = None) -> BlogPost:
    dest.mkdir(parents=True, exist_ok=True)
    own = client is None
    c = client or _client()
    kept: list[ImageRef] = []
    try:
        for img in post.images:
            try:
                r = c.get(img.url, headers={"Referer": REFERER, "User-Agent": UA})
            except httpx.HTTPError:
                continue
            if r.status_code != 200 or not r.content:
                continue
            path = dest / f"img_{len(kept):02d}.jpg"
            path.write_bytes(r.content)
            kept.append(ImageRef(url=img.url, local_path=str(path)))
    finally:
        if own:
            c.close()
    return post.model_copy(update={"images": kept})
