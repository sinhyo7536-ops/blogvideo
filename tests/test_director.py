"""링크를 넣으면 AI가 바로 대본을 쓰고, 연출 요청을 AI가 영상 설정으로 바꾼다. 'AI 없이'는 없앴다."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import director
from app.captions import render_caption_png
from app.director import Direction, SceneDirection, apply, build_request, direct, needs_direction
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, RenderOptions, Scene, VideoScript
from app.render import LOOK, STRENGTH, ZOOM, _build_segments
from tests.conftest import make_silence
from tests.fakes import fake_steps, wait_stage

URL = "https://blog.naver.com/a/1"


def _script(*directions, overall=""):
    scenes = [Scene(text=f"장면 {i} 글", image_index=0, direction=d) for i, d in enumerate(directions)]
    return VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[], direction=overall)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    manager = JobManager(tmp_path / "jobs", fake_steps())
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager
    manager.stop()


# ---------- 'AI 없이'는 없고, 링크를 넣으면 AI가 바로 쓴다 ----------
def test_no_ai_mode_is_gone(api):
    c, manager = api
    assert c.post("/api/jobs", json={"url": URL, "mode": "auto"}).status_code == 400
    job = c.post("/api/jobs", json={"url": URL, "mode": "load"}).json()
    wait_stage(manager, job["id"], "loaded")
    assert c.post(f"/api/jobs/{job['id']}/write", json={"mode": "auto"}).status_code == 400
    assert c.post(f"/api/jobs/{job['id']}/free-mode", json={"mode": "auto"}).status_code in (404, 405)


@pytest.mark.parametrize("mode,title", [("api", "제목: 테스트/글"), ("gemini", "제미나이가 쓴 제목")])
def test_link_goes_straight_to_ai_script(api, mode, title):
    c, manager = api
    job = c.post("/api/jobs", json={"url": URL, "mode": mode}).json()
    s = wait_stage(manager, job["id"], "ready")          # 중간에 고르는 단계 없이 바로 대본
    assert s.mode == mode and s.script.scenes and s.script.yt_title == title


def test_manual_from_home_loads_and_opens_empty_script(api):
    c, manager = api
    manager.steps.generate_script = lambda *a: (_ for _ in ()).throw(AssertionError("AI를 부르면 안 됨"))
    job = c.post("/api/jobs", json={"url": URL, "mode": "manual"}).json()
    s = wait_stage(manager, job["id"], "ready")
    assert s.mode == "manual" and s.script.scenes == [] and s.post.images


def test_missing_key_message_names_the_other_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    steps = fake_steps()
    steps.api_key = lambda: ""
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    r = c.post("/api/jobs", json={"url": URL, "mode": "api"})
    assert r.status_code == 400 and "제미나이" in r.json()["message"] and "AI 없이" not in r.json()["message"]
    manager.stop()


def test_old_saved_job_with_auto_mode_still_loads(tmp_path):
    from app.models import JobState

    assert JobState.model_validate({"id": "x", "url": "u", "mode": "auto"}).mode == "auto"


# ---------- 연출 요청이 있는지 ----------
def test_needs_direction():
    assert not needs_direction(None) and not needs_direction(_script("", ""))
    assert needs_direction(_script("", "천천히 확대")) and needs_direction(_script("", overall="전체적으로 잔잔하게"))
    done = _script("천천히 확대")
    done.scenes[0].direction_done = "천천히 확대"
    assert not needs_direction(done)                       # 이미 반영한 요청은 다시 묻지 않음
    done.scenes[0].direction = "빠르게"
    assert needs_direction(done)                           # 요청을 고치면 다시


def test_request_lists_every_scene_and_marks_done_ones():
    s = _script("흑백으로", "자막 크게", overall="")
    s.scenes[0].direction_done = "흑백으로"
    text = build_request(s, RenderOptions())
    data = json.loads(text[text.index("{"):])
    assert [x["index"] for x in data["장면"]] == [0, 1]
    assert data["장면"][0]["요청"] == "흑백으로 (이미 반영됨)" and data["장면"][1]["요청"] == "자막 크게"
    assert data["장면"][1]["지금 설정"]["motion"] == "auto" and data["지금 영상 설정"]["template"] == "clean"


def test_prompt_covers_all_controls_and_text_rule():
    for word in ("motion", "transition", "strength", "look", "caption_size", "caption_pos", "hold", "template",
                 "speed", "bgm_volume", "이미 반영됨", "지어내지"):
        assert word in director.SYSTEM
    scene = director.SCHEMA["properties"]["scenes"]["items"]
    assert "bw" in scene["properties"]["look"]["enum"] and "keep" in scene["properties"]["motion"]["enum"]
    assert set(scene["required"]) >= {"index", "text", "hold"}


# ---------- AI의 답을 반영 ----------
def test_apply_changes_only_what_was_decided():
    s = _script("흑백으로, 자막 크게, 오래 보여 줘", "")
    s.scenes[1].motion = "pan_left"
    d = Direction(scenes=[SceneDirection(index=0, text="장면 0 글", look="bw", caption_size="large", hold=2.5),
                          SceneDirection(index=1, text="AI가 멋대로 바꾼 글", motion="keep")])
    out, opts = apply(s, RenderOptions(), d)
    a, b = out.scenes
    assert (a.look, a.caption_size, a.hold, a.motion, a.text) == ("bw", "large", 2.5, "auto", "장면 0 글")
    assert a.direction_done == "흑백으로, 자막 크게, 오래 보여 줘"
    assert (b.motion, b.text) == ("pan_left", "장면 1 글")      # 요청 없는 장면의 글은 바뀌지 않음
    assert opts == RenderOptions() and not needs_direction(out)


def test_apply_rewrites_text_only_when_scene_was_asked():
    s = _script("더 짧게 써 줘")
    out, _ = apply(s, RenderOptions(), Direction(scenes=[SceneDirection(index=0, text="  짧은   글  ")]))
    assert out.scenes[0].text == "짧은 글"
    out, _ = apply(s, RenderOptions(), Direction(scenes=[SceneDirection(index=0, text="")]))
    assert out.scenes[0].text == "장면 0 글"                    # 빈 글로 지우지 않음


def test_apply_ignores_unknown_values_and_bad_indexes():
    s = _script("이상한 요청")
    d = Direction(scenes=[SceneDirection(index=0, text="장면 0 글", motion="spin", look="neon", hold=99),
                          SceneDirection(index=7, text="없는 장면")],
                  template="fancy", speed="turbo", bgm_volume=3)
    out, opts = apply(s, RenderOptions(), d)
    assert (out.scenes[0].motion, out.scenes[0].look, out.scenes[0].hold) == ("auto", "auto", 5.0)
    assert len(out.scenes) == 1 and opts == RenderOptions()


def test_apply_overall_request_sets_video_options_and_can_reset():
    s = _script("", overall="전체적으로 감성적이고 느리게, 음악은 작게")
    s.scenes[0].look = "bw"
    d = Direction(scenes=[SceneDirection(index=0, text="장면 0 글", look="auto", transition="fade")],
                  template="mood", speed="slow", bgm_volume=0.08)
    out, opts = apply(s, RenderOptions(), d)
    assert (opts.template, opts.speed, opts.bgm_volume) == ("mood", "slow", 0.08)
    assert (out.scenes[0].look, out.scenes[0].transition) == ("auto", "fade")
    assert out.direction_done == s.direction


# ---------- AI 호출 ----------
def test_direct_with_claude_sends_request_and_format():
    seen = {}

    def parse(**kw):
        seen.update(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=Direction(
            scenes=[SceneDirection(index=0, text="장면 0 글", motion="zoom_out", strength="strong")]))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    out, _ = direct(_script("넓게 보여 주면서 역동적으로"), RenderOptions(), "api", "k", client=client)
    assert (out.scenes[0].motion, out.scenes[0].strength) == ("zoom_out", "strong")
    assert seen["output_format"] is Direction and seen["system"] == director.SYSTEM
    assert "넓게 보여 주면서 역동적으로" in seen["messages"][0]["content"] and seen["model"] == "claude-opus-5-5"


def test_direct_with_gemini_uses_schema():
    seen = {}

    def gen(*, model, contents, config):
        seen.update(contents=contents, config=config)
        return SimpleNamespace(text=json.dumps({"scenes": [
            {"index": 0, "text": "장면 0 글", "motion": "keep", "transition": "circle", "strength": "keep",
             "look": "warm", "caption_size": "keep", "caption_pos": "top", "hold": -1}],
            "template": "keep", "speed": "keep", "bgm_volume": -1}, ensure_ascii=False))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    out, opts = direct(_script("따뜻하게, 자막은 위에"), RenderOptions(), "gemini", "k", client=client)
    sc = out.scenes[0]
    assert (sc.look, sc.caption_pos, sc.transition, sc.hold) == ("warm", "top", "circle", 0.0)
    assert seen["config"].response_json_schema is director.SCHEMA and opts == RenderOptions()


def test_direct_retries_when_answer_misses_most_scenes():
    answers = [Direction(scenes=[]), Direction(scenes=[SceneDirection(index=i, text=f"장면 {i} 글") for i in range(3)])]
    calls = []

    def parse(**kw):
        calls.append(1)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=answers.pop(0))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    direct(_script("a", "b", "c"), RenderOptions(), "api", "k", client=client)
    assert len(calls) == 2


# ---------- 영상 만들기를 누르면 연출이 먼저 반영된다 ----------
def _ready_with(c, manager, mode="api", **scene):
    job = c.post("/api/jobs", json={"url": URL, "mode": mode}).json()
    s = wait_stage(manager, job["id"], "ready")
    script = s.script.model_dump()
    script["scenes"][0].update(scene)
    c.put(f"/api/jobs/{job['id']}/script", json={"script": script, "options": job["options"]})
    return job["id"]


def test_render_runs_director_first_and_keeps_result(api):
    c, manager = api
    calls = []

    def fake_direct(script, options, engine, key):
        calls.append((engine, key, script.scenes[0].direction))
        return apply(script, options, Direction(scenes=[SceneDirection(index=0, text="AI가 줄인 글", look="bw")],
                                                template="mood"))

    spoken = []
    real_tts = manager.steps.synthesize_all
    manager.steps.synthesize_all = lambda script, *a, **k: (spoken.append(script.scenes[0].text), real_tts(script, *a, **k))[1]
    manager.steps.direct = fake_direct
    job_id = _ready_with(c, manager, direction="흑백으로, 더 짧게")
    c.post(f"/api/jobs/{job_id}/render")
    s = wait_stage(manager, job_id, "done")
    assert calls == [("api", "sk-ant-test1234", "흑백으로, 더 짧게")]
    assert spoken == ["AI가 줄인 글"]                              # 성우는 AI가 고친 글을 읽음
    assert (s.script.scenes[0].look, s.script.scenes[0].text, s.options.template) == ("bw", "AI가 줄인 글", "mood")
    c.post(f"/api/jobs/{job_id}/render")                          # 요청을 안 고치고 다시 만들면 AI를 또 부르지 않음
    wait_stage(manager, job_id, "done")
    assert len(calls) == 1


def test_render_without_requests_never_calls_ai(api):
    c, manager = api
    manager.steps.direct = lambda *a: (_ for _ in ()).throw(AssertionError("연출 요청이 없으면 부르면 안 됨"))
    job_id = _ready_with(c, manager)
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")


def test_director_uses_the_ai_that_wrote_the_script_or_any_key(api):
    c, manager = api
    engines = []
    manager.steps.direct = lambda script, options, engine, key: (engines.append((engine, key)), apply(
        script, options, Direction(scenes=[])))[1]
    job_id = _ready_with(c, manager, mode="gemini", direction="크게")
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")
    manager.steps.gemini_key = lambda: ""                          # 직접 작성 + Claude 키만 있음
    job_id = _ready_with(c, manager, mode="api", direction="크게")
    manager._set(job_id, mode="manual")
    c.post(f"/api/jobs/{job_id}/render")
    wait_stage(manager, job_id, "done")
    assert engines == [("gemini", "AIza-test"), ("api", "sk-ant-test1234")]


def test_director_failure_stops_render_with_clear_message(api):
    c, manager = api
    from app.errors import UserError

    manager.steps.direct = lambda *a: (_ for _ in ()).throw(UserError("AI 서버가 바빠요. 잠시 뒤 다시 시도해 주세요."))
    job_id = _ready_with(c, manager, direction="크게")
    c.post(f"/api/jobs/{job_id}/render")
    s = wait_stage(manager, job_id, "error")
    assert s.failed_phase == "render" and "바빠요" in s.message and s.script.scenes[0].direction == "크게"


def test_no_key_at_all_asks_to_add_one(api):
    c, manager = api
    job_id = _ready_with(c, manager, direction="크게")
    manager.steps.direct = lambda *a: (_ for _ in ()).throw(AssertionError("키가 없으면 부르면 안 됨"))
    manager.steps.api_key = lambda: ""
    manager.steps.gemini_key = lambda: ""
    c.post(f"/api/jobs/{job_id}/render")
    assert "키" in wait_stage(manager, job_id, "error").message


# ---------- 정해진 연출이 영상에 쓰인다 ----------
def test_direction_fields_reach_the_renderer(tmp_path):
    img = tmp_path / "p.jpg"
    Image.new("RGB", (1200, 900), "orange").save(img)
    post = BlogPost(title="t", paragraphs=[], url="u", images=[ImageRef(url="i", local_path=str(img))])
    audio = str(make_silence(tmp_path / "a.mp3", 1.0))
    scenes = [Scene(text="a", image_index=0, duration=1.4, audio_path=audio, look="bw", strength="strong", hold=2.0,
                    caption_size="large", caption_pos="top"),
              Scene(text="b", image_index=0, duration=1.4, audio_path=audio)]
    script = VideoScript(scenes=scenes, yt_title="t", yt_description="d", yt_tags=[])
    a, b = _build_segments(post, script, RenderOptions(quality="720p", intro=False, outro=False), (1280, 720),
                           tmp_path / "w")
    assert (a.duration, a.zoom, a.look) == (3.4, STRENGTH["strong"], LOOK["bw"])
    assert (b.duration, b.zoom, b.look) == (1.4, ZOOM, "")


def test_look_and_hold_change_the_video(tmp_path):
    import subprocess

    from app.render import render_video

    img = tmp_path / "p.jpg"
    Image.new("RGB", (1200, 900), (230, 120, 30)).save(img)
    post = BlogPost(title="t", paragraphs=[], url="u", images=[ImageRef(url="i", local_path=str(img))])
    audio = str(make_silence(tmp_path / "a.mp3", 1.0))
    script = VideoScript(scenes=[Scene(text="", image_index=0, duration=1.4, audio_path=audio, look="bw", hold=1.5)],
                         yt_title="t", yt_description="d", yt_tags=[])
    out = render_video(post, script, RenderOptions(quality="720p", intro=False, outro=False, title_bar=False),
                       tmp_path / "w")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 2.9) < 0.3                                    # 1.4초 + 머무름 1.5초
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    r, g, b = np.frombuffer(raw, np.uint8).reshape(720, 1280, 3)[300, 640].astype(int)
    assert max(r, g, b) - min(r, g, b) < 12                        # 흑백


@pytest.mark.parametrize("look", list(LOOK))
def test_every_look_filter_is_valid_ffmpeg(tmp_path, look):
    import subprocess

    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=orange:s=64x64:d=0.1",
                        "-vf", LOOK[look], "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_caption_size_and_position(tmp_path):
    def box(**kw):
        return Image.open(render_caption_png("자막 테스트예요", 1280, 720, tmp_path / "c.png", "clean", **kw)).getbbox()

    normal, large, small = box(), box(size="large"), box(size="small")
    assert (large[3] - large[1]) > (normal[3] - normal[1]) > (small[3] - small[1])
    top, center = box(pos="top"), box(pos="center")
    assert top[1] < center[1] < normal[1]
    assert top[1] > 720 * 0.2                                      # 왼쪽 위 제목 띠를 가리지 않음
    assert abs((center[1] + center[3]) / 2 - 360) < 12
    for tpl in ("mood", "vlog"):
        im = Image.open(render_caption_png("자막", 720, 1280, tmp_path / "v.png", tpl, size="large", pos="top"))
        assert im.getbbox() is not None and im.getbbox()[3] < 1280 * 0.6


def test_place_names_not_in_the_post_are_dropped():
    from app.script import ScriptDraft, SceneDraft, draft_to_script

    post = BlogPost(title="통영 여행", paragraphs=["동피랑 벽화마을에 갔어요.", "중앙시장에서 회를 먹었어요."], url="u",
                    images=[ImageRef(url="i")])
    draft = ScriptDraft(scenes=[SceneDraft(text="a", image_index=0, place="통영 동피랑"),
                                SceneDraft(text="b", image_index=0, place="머그컵가이 작업실"),
                                SceneDraft(text="c", image_index=0, place="중앙시장"),
                                SceneDraft(text="d", image_index=0, place="")],
                        yt_title="t", yt_description="d", yt_tags=[])
    assert [s.place for s in draft_to_script(draft, post).scenes] == ["통영 동피랑", "", "중앙시장", ""]
