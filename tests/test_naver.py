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


def _jpg(seed: int, size=(64, 48)) -> bytes:
    """서로 다른 진짜 JPEG (지문이 겹치지 않게 무늬를 다르게)."""
    import io

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    a = (rng.random((6, 8)) * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(a).resize(size, Image.BICUBIC).convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


def test_download_images_skips_failures(tmp_path):
    def handler(r):
        assert r.headers["referer"] == "https://m.blog.naver.com/"
        if "bad" in str(r.url):
            return httpx.Response(404)
        return httpx.Response(200, content=_jpg(1 if "ok1" in str(r.url) else 2))

    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[
        ImageRef(url="https://x/ok1.jpg"), ImageRef(url="https://x/bad.jpg"), ImageRef(url="https://x/ok2.jpg")])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert [i.url for i in out.images] == ["https://x/ok1.jpg", "https://x/ok2.jpg"]
    assert all(Path(i.local_path).read_bytes().startswith(b"\xff\xd8") for i in out.images)
    assert out.missing == ["2번째 사진"] and out.duplicates == 0


def test_download_prefers_original_size(tmp_path):
    seen = []

    def handler(r):
        seen.append(str(r.url))
        if r.url.host == "postfiles.pstatic.net" and "w3840" in str(r.url):
            return httpx.Response(200, content=_jpg(3, (128, 96)))
        return httpx.Response(200, content=_jpg(3))

    u = "https://mblogthumb-phinf.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w966"
    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[ImageRef(url=u)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert seen[0] == "https://postfiles.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w3840"
    assert Path(out.images[0].local_path).read_bytes() == _jpg(3, (128, 96))


def test_download_falls_back_when_original_missing(tmp_path):
    def handler(r):
        if r.url.host == "postfiles.pstatic.net":
            return httpx.Response(404)
        return httpx.Response(200, content=_jpg(4))

    u = "https://mblogthumb-phinf.pstatic.net/MjAy/abc.JPEG/1.jpg?type=w966"
    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[ImageRef(url=u)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert Path(out.images[0].local_path).read_bytes() == _jpg(4)



# ---------- 4단계: 작성 날짜, 사진 위치, 못 받은 사진, 중복 사진 ----------
def test_post_date_and_photo_positions():
    html = Path(__file__).parent.joinpath("fixtures", "naver_post.html").read_text("utf-8")
    post = extract_post(html, "u")
    assert post.date == "2026. 11. 4. 10:22"
    paras = [i.para for i in post.images]
    assert paras == sorted(paras) and paras[0] >= 0 and all(-1 <= p < len(post.paragraphs) for p in paras)


def test_photo_position_counts_paragraphs_before_it():
    html = ('<div class="se-main-container"><img class="se-image-resource" src="https://x/a.jpg">'
            '<p class="se-text-paragraph">첫 문단</p><p class="se-text-paragraph">  </p>'
            '<p class="se-text-paragraph">둘째 문단</p><img class="se-image-resource" src="https://x/b.jpg"></div>')
    post = extract_post(html, "u")
    assert post.paragraphs == ["첫 문단", "둘째 문단"] and [i.para for i in post.images] == [-1, 1]


def test_duplicate_and_unreadable_photos_are_reported(tmp_path):
    big = _jpg(7, (640, 480))
    same_small = _jpg(7, (320, 240))                     # 같은 사진, 크기만 다름
    def handler(r):
        name = str(r.url)
        if "broken" in name:
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=same_small if "copy" in name else big if "first" in name else _jpg(8))

    post = BlogPost(title="t", paragraphs=["p"], url="u", images=[
        ImageRef(url="https://x/first.jpg", para=0), ImageRef(url="https://x/copy.jpg", para=0),
        ImageRef(url="https://x/broken.jpg", para=3), ImageRef(url="https://x/other.jpg", para=0)])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download_images(post, tmp_path, client=c)
    assert [i.url for i in out.images] == ["https://x/first.jpg", "https://x/other.jpg"]
    assert out.duplicates == 1 and out.missing == ["3번째 사진 (열 수 없는 파일)"]
    assert [i.para for i in out.images] == [0, 0]
    # 다른 글에서 이미 받은 사진과 같은 것도 뺀다
    from app.scraper.naver import image_hash

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        again = download_images(post.model_copy(update={"images": post.images[:1]}), tmp_path / "b", client=c,
                                known=[image_hash(big)])
    assert again.images == [] and again.duplicates == 1



@pytest.mark.parametrize("text,expected", [("12시간 전", "2026. 11. 14."), ("30분 전", "2026. 11. 15."), ("3일 전", "2026. 11. 12."),
                                           ("어제", "2026. 11. 14."), ("방금 전", "2026. 11. 15."),
                                           ("2026. 10. 3. 14:05", "2026. 10. 3. 14:05")])
def test_relative_dates_become_real_dates(text, expected):
    import datetime as dt

    from app.scraper.naver import absolute_date

    assert absolute_date(text, now=dt.datetime(2026, 11, 15, 9, 0)) == expected
