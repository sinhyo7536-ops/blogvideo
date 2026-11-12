from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.errors import UserError
from app.models import BlogPost, ImageRef, Scene, VideoScript
from app.script import ScriptDraft, SceneDraft, generate_script, normalize_script


def vs(scenes, title="t", tags=None):
    return VideoScript(scenes=scenes, yt_title=title, yt_description="d", yt_tags=tags or [])


def test_normalize_clamps_image_index():
    s = normalize_script(vs([Scene(text="a", image_index=9), Scene(text="b", image_index=-1)]), image_count=3)
    assert [x.image_index for x in s.scenes] == [0, 1]


def test_normalize_no_images_sets_none():
    s = normalize_script(vs([Scene(text="a", image_index=0)]), image_count=0)
    assert s.scenes[0].image_index is None


def test_normalize_trims_title_and_drops_empty_scenes():
    s = normalize_script(vs([Scene(text="  ", image_index=0), Scene(text="b", image_index=0)],
                            title="가" * 80, tags=["#태그", "태그", " "]), 1)
    assert len(s.scenes) == 1 and len(s.yt_title) <= 60 and s.yt_tags == ["태그"]


@pytest.fixture
def sample_post():
    return BlogPost(title="제목", paragraphs=["첫 문단", "둘째 문단"], url="https://blog.naver.com/a/1",
                    images=[ImageRef(url="u1"), ImageRef(url="u2")])


GOOD = ScriptDraft(scenes=[SceneDraft(text="안녕하세요", image_index=0), SceneDraft(text="끝", image_index=1)],
                   yt_title="제목", yt_description="설명", yt_tags=["a"])


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def resp(parsed, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed)


def test_generate_success_uses_model_and_converts(sample_post):
    c = FakeClient([resp(GOOD)])
    s = generate_script(sample_post, "k", client=c)
    assert [x.text for x in s.scenes] == ["안녕하세요", "끝"]
    assert c.kwargs["model"] == "claude-opus-5-5"
    assert c.kwargs["output_format"] is ScriptDraft
    assert "https://blog.naver.com/a/1" in str(c.kwargs["messages"])


def test_generate_retries_once_on_invalid_then_succeeds(sample_post):
    c = FakeClient([resp(None), resp(GOOD)])
    assert len(generate_script(sample_post, "k", client=c).scenes) == 2 and c.calls == 2


def test_generate_fails_after_two_invalid(sample_post):
    c = FakeClient([resp(GOOD, stop="refusal"), resp(ScriptDraft(scenes=[], yt_title="", yt_description="", yt_tags=[]))])
    with pytest.raises(UserError, match="대본을 만들지 못했어요"):
        generate_script(sample_post, "k", client=c)


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_generate_auth_error_message(sample_post):
    err = anthropic.AuthenticationError("bad", response=httpx.Response(401, request=_req()), body=None)
    with pytest.raises(UserError, match="API 키를 확인"):
        generate_script(sample_post, "k", client=FakeClient([err]))


def test_generate_connection_error_message(sample_post):
    with pytest.raises(UserError, match="인터넷 연결"):
        generate_script(sample_post, "k", client=FakeClient([anthropic.APIConnectionError(request=_req())]))


def test_generate_without_key(sample_post):
    with pytest.raises(UserError, match="API 키"):
        generate_script(sample_post, "")


# ---------- 여행 영상: 장소 이름, 시작 화면 제목 ----------
TRIP = ScriptDraft(scenes=[SceneDraft(text="통영에 다녀왔어요", image_index=0, place="  통영   동피랑 "),
                           SceneDraft(text="끝", image_index=1)],
                   yt_title="제목", yt_description="설명", yt_tags=["a"],
                   intro_title="통영 1박 2일 여행", intro_sub="가을 · 친구와")


def test_generate_keeps_place_and_intro(sample_post):
    s = generate_script(sample_post, "k", client=FakeClient([resp(TRIP)]))
    assert [x.place for x in s.scenes] == ["통영 동피랑", ""]
    assert (s.intro_title, s.intro_sub) == ("통영 1박 2일 여행", "가을 · 친구와")


def test_intro_title_falls_back_to_post_title(sample_post):
    s = generate_script(sample_post, "k", client=FakeClient([resp(GOOD)]))
    assert s.intro_title == "제목" and s.intro_sub == ""


def test_prompt_asks_for_travel_fields():
    from app.script import SYSTEM

    assert "여행" in SYSTEM and "place" in SYSTEM and "intro_title" in SYSTEM


def test_auto_script_has_intro_title(sample_post):
    from app.autoscript import auto_script

    s = auto_script(sample_post)
    assert s.intro_title == "제목" and all(x.place == "" for x in s.scenes)


def test_blog_name_is_read_from_post():
    from pathlib import Path

    from app.scraper.naver import extract_post

    html = (Path(__file__).parent / "fixtures" / "naver_post.html").read_text("utf-8")
    assert extract_post(html, "u").blog_name


@pytest.mark.parametrize("title,expected", [
    ("통영 1박 2일 여행", "통영 1박 2일 여행"),
    ("[국내여행] 통영 1박 2일 여행 - 동피랑, 중앙시장 후기", "통영 1박 2일 여행"),
    ("(내돈내산)[경남] 통영 여행 | 먹방 코스", "통영 여행"),
    ("가을에 다녀온 통영 동피랑 벽화마을과 중앙시장 그리고 케이블카", "가을에 다녀온 통영 동피랑 벽화마을과…"),
])
def test_short_title(title, expected):
    from app.script import short_title

    assert short_title(title) == expected
    assert len(short_title(title)) <= 23


def test_blog_name_drops_naver_prefix():
    from app.scraper.naver import extract_post

    html = ('<meta property="og:site_name" content="네이버 블로그 | 신효의 여행노트">'
            '<div class="se-main-container"><p class="se-text-paragraph">본문</p></div>')
    assert extract_post(html, "u").blog_name == "신효의 여행노트"
