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
