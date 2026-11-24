"""대본 칸에서 줄을 바꾸면(엔터) 영상 자막도 그 자리에서 줄이 바뀐다 (사용자 요청, 2026-11-24).
목소리는 줄바꿈과 상관없이 한 문장처럼 읽고, 줄바꿈만 고쳤을 때 목소리를 새로 만들지 않는다."""
from app.captions import _layout, render_caption_png, supported_text
from app.models import Scene, VideoScript
from app.tts import spoken_text, synthesize_all, voice_file_key


def test_supported_text_keeps_line_breaks_only_for_captions():
    assert supported_text("첫 줄  입니다\n\n\n 둘째 줄 ", keep_lines=True) == "첫 줄 입니다\n둘째 줄"
    assert supported_text("첫 줄\n둘째 줄") == "첫 줄 둘째 줄"         # 제목 등은 예전처럼 한 줄


def test_layout_breaks_where_the_user_pressed_enter():
    _, lines = _layout("바다가 보여요\n정말 예뻐요", 2000, 64)
    assert lines == ["바다가 보여요", "정말 예뻐요"]


def test_long_user_line_still_wraps_inside():
    _, lines = _layout("짧은 줄\n" + "아주 긴 줄입니다 " * 6, 600, 64)
    assert lines[0] == "짧은 줄" and len(lines) >= 3


def test_more_user_lines_than_usual_limit_are_kept():
    _, lines = _layout("하나\n둘\n셋\n넷", 2000, 64)
    assert lines == ["하나", "둘", "셋", "넷"]


def test_caption_png_with_line_breaks(tmp_path):
    out = render_caption_png("첫 줄\n둘째 줄", 1280, 720, tmp_path / "c.png")
    assert out.exists()


def test_voice_reads_line_breaks_as_one_sentence():
    assert spoken_text("바다가 보여요\n정말 예뻐요") == "바다가 보여요 정말 예뻐요"
    assert voice_file_key(spoken_text("가\n나"), "v", {}) == voice_file_key(spoken_text("가 나"), "v", {})


def test_synthesize_all_sends_text_without_line_breaks(tmp_path, monkeypatch):
    got = []

    async def synth(text, voice, out, **kw):
        got.append(text)
        out.write_bytes(b"x")

    monkeypatch.setattr("app.tts.probe_duration", lambda p: 1.0)
    script = VideoScript(scenes=[Scene(text="첫 줄\n둘째 줄", image_index=None)], yt_title="t", yt_description="", yt_tags=[])
    synthesize_all(script, "female", tmp_path, synth=synth)
    assert got == ["첫 줄 둘째 줄"]
