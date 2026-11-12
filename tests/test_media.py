"""목소리 넓히기(종류·속도·미리 듣기), 유튜브 링크 배경음악, 글 전체 담기."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ytmusic
from app.errors import UserError
from app.jobs import JobManager
from app.main import create_app
from app.models import VOICE_KEYS, RenderOptions
from app.script import SYSTEM
from app.tts import SPEEDS, VOICE_LIST, synthesize_all
from tests.conftest import make_silence
from tests.fakes import fake_steps, wait_stage


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    manager = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager
    manager.stop()


# ---------- 목소리 ----------
def test_voice_catalog():
    assert tuple(VOICE_LIST) == VOICE_KEYS and len(VOICE_LIST) >= 12
    korean = [v for v in VOICE_LIST.values() if v.group == "한국어"]
    assert len(korean) >= 6 and all(v.name.startswith("ko-KR-") for v in korean)
    assert {v.group for v in VOICE_LIST.values()} == {"한국어", "다국어"}
    assert len({v.label for v in VOICE_LIST.values()}) == len(VOICE_LIST)      # 화면 이름이 겹치지 않음


def test_options_accept_new_voices_and_speed():
    o = RenderOptions(voice="hyunsu", speed="fast")
    assert (o.voice, o.speed) == ("hyunsu", "fast")
    assert RenderOptions().speed == "normal" and RenderOptions(voice="male").voice == "male"   # 예전 값 그대로
    for bad in ({"voice": "robot"}, {"speed": "turbo"}):
        with pytest.raises(Exception):
            RenderOptions(**bad)


def test_speed_and_pitch_reach_the_synthesizer(tmp_path, short_script):
    calls = []

    async def rec(text, voice, out, **kw):
        calls.append((voice, kw))
        make_silence(Path(out), 0.3)

    synthesize_all(short_script, "female_bright", tmp_path / "a", synth=rec, speed="fast")
    assert calls[0] == ("ko-KR-SunHiNeural", {"rate": SPEEDS["fast"], "pitch": "+14Hz"})
    calls.clear()
    synthesize_all(short_script, "andrew", tmp_path / "b", synth=rec, speed="slow")
    assert calls[0] == ("en-US-AndrewMultilingualNeural", {"rate": SPEEDS["slow"]})
    calls.clear()
    synthesize_all(short_script, "female", tmp_path / "c", synth=rec)      # 기본값이면 아무것도 덧붙이지 않음
    assert calls[0] == ("ko-KR-SunHiNeural", {})


def test_job_passes_voice_and_speed_to_tts(api):
    c, manager = api
    seen = {}
    real = manager.steps.synthesize_all

    def spy(script, voice, dest, on_progress=None, speed="normal"):
        seen.update(voice=voice, speed=speed)
        return real(script, voice, dest, on_progress, speed)

    manager.steps.synthesize_all = spy
    job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "auto"}).json()
    s = wait_stage(manager, job["id"], "ready")
    opts = {**job["options"], "voice": "hyunsu", "speed": "slow"}
    r = c.put(f"/api/jobs/{job['id']}/script", json={"script": s.script.model_dump(), "options": opts})
    assert r.status_code == 200
    c.post(f"/api/jobs/{job['id']}/render")
    wait_stage(manager, job["id"], "done")
    assert seen == {"voice": "hyunsu", "speed": "slow"}


def test_voices_endpoint_and_preview(api, tmp_path):
    c, manager = api
    voices = c.get("/api/voices").json()
    assert [v["key"] for v in voices] == list(VOICE_KEYS) and voices[0]["label"] and voices[0]["group"] == "한국어"
    made = []
    real = manager.steps.voice_preview
    manager.steps.voice_preview = lambda v, s, out: (made.append((v, s)), real(v, s, out))[1]
    r = c.post("/api/voice-preview", json={"voice": "hyunsu", "speed": "fast"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"mp3:hyunsu:fast"
    c.post("/api/voice-preview", json={"voice": "hyunsu", "speed": "fast"})
    assert made == [("hyunsu", "fast")]                       # 두 번째는 저장해 둔 것을 씀
    assert c.post("/api/voice-preview", json={"voice": "robot"}).status_code == 400


# ---------- 유튜브 링크 배경음악 ----------
@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", "youtu.be/abc", "https://music.youtube.com/watch?v=abc",
                                 "https://m.youtube.com/watch?v=abc"])
def test_youtube_links_accepted(url):
    assert ytmusic.normalize(url).startswith("https://")


@pytest.mark.parametrize("url", ["", "https://example.com/watch?v=abc", "https://youtube.com.evil.com/x",
                                 "file:///C:/Windows/win.ini", "ftp://youtube.com/x", "https://vimeo.com/1"])
def test_other_links_rejected(url):
    with pytest.raises(UserError, match="유튜브 영상 주소"):
        ytmusic.normalize(url)


def test_music_name_is_a_safe_file_name():
    name = ytmusic.safe_name('..\\여행/브이로그: "봄" <BGM>?  ' + "가" * 100)
    assert name.endswith(".mp3") and not name.startswith(".") and len(name) <= 64
    assert not any(ch in name for ch in '\\/:*?"<>|')
    assert RenderOptions(bgm=name).bgm == name                 # 배경음악 이름 규칙을 통과
    assert ytmusic.safe_name("") == "유튜브 음악.mp3"


def test_fetch_audio_saves_mp3_with_title(tmp_path, monkeypatch):
    import yt_dlp

    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            seen["url"], seen["download"] = url, download
            return {"title": "봄 여행 BGM: 산책", "duration": 180}

        def process_ie_result(self, info, download):
            Path(seen["opts"]["outtmpl"].replace(".%(ext)s", ".mp3")).write_bytes(b"audio")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    name = ytmusic.fetch_audio("https://youtu.be/abc", tmp_path / "music")
    assert name == "봄 여행 BGM 산책.mp3" and (tmp_path / "music" / name).read_bytes() == b"audio"
    assert seen["download"] is False and seen["opts"]["noplaylist"] is True
    assert seen["opts"]["postprocessors"][0]["preferredcodec"] == "mp3"
    assert not list((tmp_path / "music").glob("_yt_download*"))


def test_fetch_audio_refuses_very_long_or_broken(tmp_path, monkeypatch):
    import yt_dlp

    class Long:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download): return {"title": "t", "duration": 3 * 3600}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Long)
    with pytest.raises(UserError, match="30분"):
        ytmusic.fetch_audio("https://youtu.be/abc", tmp_path)

    class Broken(Long):
        def extract_info(self, url, download): raise RuntimeError("Video unavailable")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Broken)
    with pytest.raises(UserError, match="가져오지 못했어요"):
        ytmusic.fetch_audio("https://youtu.be/abc", tmp_path)


def test_youtube_music_endpoint_adds_to_list_and_plays(api):
    c, _ = api
    r = c.post("/api/music/youtube", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert r.status_code == 200 and r.json() == {"name": "유튜브 곡.mp3", "builtin": False}
    assert {"name": "유튜브 곡.mp3", "builtin": False} in c.get("/api/music").json()
    f = c.get("/api/music/file/유튜브 곡.mp3")
    assert f.status_code == 200 and f.headers["content-type"] == "audio/mpeg" and f.content.startswith(b"mp3 from")
    assert c.post("/api/music/youtube", json={"url": "https://example.com/x"}).status_code == 400
    bad = c.post("/api/music/youtube", json={"url": "https://youtu.be/bad"})
    assert bad.status_code == 400 and "가져오지 못했어요" in bad.json()["message"]


def test_music_file_endpoint_only_serves_music_folder(api):
    c, _ = api
    assert c.get("/api/music/file/없는곡.mp3").status_code == 404
    assert c.get("/api/music/file/..%5C..%5Cconfig.json").status_code == 404
    builtin = [m["name"] for m in c.get("/api/music").json() if m["builtin"]]
    assert builtin and c.get(f"/api/music/file/{builtin[0]}").status_code == 200     # 내장 샘플 음악


# ---------- 글 전체 담기 ----------
def test_prompt_asks_for_whole_post_without_length_limit():
    assert "빠짐없이" in SYSTEM and "제한이 없" in SYSTEM
    assert "6~12" not in SYSTEM
