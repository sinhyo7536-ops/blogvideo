from pathlib import Path

import pytest

from app.errors import UserError
from app.tts import VOICES, synthesize_all
from tests.conftest import make_silence


async def fake_synth_1s(text, voice, out):
    make_silence(Path(out), 1.0)


async def always_failing_synth(text, voice, out):
    raise ConnectionError("boom")


def test_voices():
    assert VOICES["female"] == "ko-KR-SunHiNeural" and VOICES["male"] == "ko-KR-InJoonNeural"
    assert len(VOICES) >= 10


def test_synthesize_all_sets_durations(tmp_path, short_script):
    s = synthesize_all(short_script, "female", tmp_path, synth=fake_synth_1s)
    assert all(abs(x.duration - 1.4) < 0.1 for x in s.scenes)
    assert [Path(x.audio_path).name for x in s.scenes] == ["voice_00.mp3", "voice_01.mp3"]
    assert all(Path(x.audio_path).exists() for x in s.scenes)
    assert short_script.scenes[0].duration is None  # 원본은 그대로


def test_synthesize_retries_then_succeeds(tmp_path, short_script):
    calls = {"n": 0}

    async def flaky(text, voice, out):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("once")
        make_silence(Path(out))

    assert synthesize_all(short_script, "male", tmp_path, synth=flaky).scenes[1].duration


def test_synthesize_retries_then_fails(tmp_path, short_script):
    with pytest.raises(UserError, match="음성을 만들지 못했어요"):
        synthesize_all(short_script, "female", tmp_path, synth=always_failing_synth)


def test_progress_reported(tmp_path, short_script):
    seen = []
    synthesize_all(short_script, "male", tmp_path, on_progress=seen.append, synth=fake_synth_1s)
    assert seen == [0.5, 1.0]


def test_passes_voice_name(tmp_path, short_script):
    voices = []

    async def rec(text, voice, out):
        voices.append(voice)
        make_silence(Path(out))

    synthesize_all(short_script, "male", tmp_path, synth=rec)
    assert set(voices) == {"ko-KR-InJoonNeural"}
