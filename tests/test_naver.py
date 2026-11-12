from pathlib import Path

import httpx
import pytest

from app.errors import UserError
from app.models import BlogPost, ImageRef
from app.scraper.naver import download_images, extract_post, parse_naver_url, resolve_url

FIXTURE = Path(__file__).parent / "fixtures" / "naver_post.html"


@pytest.mark.parametrize("url", [
    "https://blog.naver.com/abc123/223456789012",
    "https://m.blog.naver.com/abc123/223456789012",
    "blog.naver.com/abc123/223456789012",
    "https://blog.naver.com/abc123/223456789012?trackingCode=rss",
    "https://blog.naver.com/PostView.naver?blogId=abc123&logNo=223456789012",
    "https://m.blog.naver.com/PostView.naver?blogId=abc123&logNo=223456789012&navType=by",
    "  https://blog.naver.com/abc123/223456789012  ",
])
def test_parse_url_variants(url):
    assert parse_naver_url(url) == ("abc123", "223456789012")


@pytest.mark.parametrize("url", ["https://tistory.com/x/1", "그냥 글자", "https://blog.naver.com/abc123", ""])
def test_parse_url_rejects(url):
    with pytest.raises(UserError, match="네이버 블로그 글 주소를 넣어 주세요"):
        parse_naver_url(url)


def test_resolve_short_link():
    def handler(r):
        if r.url.host == "naver.me":
            return httpx.Response(302, headers={"location": "https://blog.naver.com/abc123/223456789012"})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        assert parse_naver_url(resolve_url("https://naver.me/xYz", c)) == ("abc123", "223456789012")


def test_resolve_non_short_link_untouched():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as c:
        u = "https://blog.naver.com/abc123/223456789012"
        assert resolve_url(u, c) == u


def test_extract_post_fixture():
    post = extract_post(FIXTURE.read_text("utf-8"), "https://blog.naver.com/naverofficial/224464411656")
    assert post.title == "[스페셜로고] '소상공인의 날' 로고 제작 이야기 - 머그컵가이 작가"
    assert post.paragraphs[0] == "작가님 소개를 부탁합니다."
    assert len(post.paragraphs) >= 3
    assert all(p.strip() and "​" not in p for p in post.paragraphs)
    assert len(post.images) == 13
    assert all(i.url.startswith("https://") and i.url.endswith("type=w966") for i in post.images)
    assert post.url == "https://blog.naver.com/naverofficial/224464411656"


def test_extract_post_without_container_raises():
    with pytest.raises(UserError, match="글을 읽을 수 없어요"):
        extract_post("<html><body>비공개</body></html>", "u")


def test_download_images_skips_failures(tmp_path):
    def handler(r):
        assert r.headers["referer"] == "https://m.blog.naver.com/"
        if "bad" in str(r.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"\xff\xd8\xff fakejpeg")

    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[
        ImageRef(url="https://x/ok1.jpg"), ImageRef(url="https://x/bad.jpg"), ImageRef(url="https://x/ok2.jpg")])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert [i.url for i in out.images] == ["https://x/ok1.jpg", "https://x/ok2.jpg"]
    assert all(Path(i.local_path).read_bytes().startswith(b"\xff\xd8") for i in out.images)


def test_download_prefers_original_size(tmp_path):
    seen = []

    def handler(r):
        seen.append(str(r.url))
        if r.url.host == "postfiles.pstatic.net" and "w3840" in str(r.url):
            return httpx.Response(200, content=b"\xff\xd8\xff BIG")
        return httpx.Response(200, content=b"\xff\xd8\xff small")

    u = "https://mblogthumb-phinf.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w966"
    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[ImageRef(url=u)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert seen[0] == "https://postfiles.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w3840"
    assert Path(out.images[0].local_path).read_bytes().endswith(b"BIG")


def test_download_falls_back_when_original_missing(tmp_path):
    def handler(r):
        if r.url.host == "postfiles.pstatic.net":
            return httpx.Response(404)
        return httpx.Response(200, content=b"\xff\xd8\xff small")

    u = "https://mblogthumb-phinf.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w966"
    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[ImageRef(url=u)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert Path(out.images[0].local_path).read_bytes().endswith(b"small")
