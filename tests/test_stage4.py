"""4단계: AI 여행 분석, 대본 스타일·설정, 분석 → 대본 흐름."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai import analyze as an
from app.jobs import JobManager
from app.main import create_app
from app.models import BlogPost, ImageRef, ScriptSettings, VideoScript
from app.script import SYSTEM, ScriptDraft, SceneDraft, generate_script, writing_brief
from tests.fakes import fake_steps, wait_stage

URL = "https://blog.naver.com/a/1"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))


class FakeClaude:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.answers.pop(0))


def _post(n_img=3):
    return BlogPost(title="통영 1박 2일", paragraphs=["동피랑에 갔어요.", "중앙시장에서 회를 먹었어요.", "케이블카를 탔어요."],
                    url=URL, date="2026. 10. 3.", images=[ImageRef(url=f"u{i}", para=i) for i in range(n_img)],
                    missing=["4번째 사진"])


# ---------- 분석 ----------
def test_analysis_request_has_date_positions_and_missing_photos():
    text = an._request(_post())
    assert "작성 날짜: 2026. 10. 3." in text and "사진 1: 문단 1 뒤" in text and "받지 못한 사진: 4번째 사진" in text
    assert "[0] 동피랑에 갔어요." in text


def test_analysis_prompt_forbids_invention_and_marks_evidence():
    for w in ("만들어 내지 않습니다", "글에 나옴", "사진에서 봄", "추론", "missing_info", "식당"):
        assert w in an.SYSTEM
    for c in an.PHOTO_CATEGORIES:
        assert c in an.SYSTEM


def _wire(a: "an.TravelAnalysis") -> "an.AnalysisDraft":
    return an.AnalysisDraft.model_validate(a.model_dump())


def test_analysis_wire_format_has_no_optional_fields():
    """선택 항목이 있으면 API가 'Schema is too complex' 로 거절한다."""
    def walk(schema, defs):
        if "$ref" in schema:
            schema = defs[schema["$ref"].split("/")[-1]]
        if schema.get("type") == "object":
            assert set(schema["required"]) == set(schema["properties"]), schema.get("title")
            for v in schema["properties"].values():
                walk(v, defs)
        if schema.get("type") == "array":
            walk(schema["items"], defs)

    sch = an.AnalysisDraft.model_json_schema()
    walk(sch, sch.get("$defs", {}))


def test_analyze_uses_analysis_model_and_cleans_indexes():
    answer = an.TravelAnalysis(
        theme="통영 바다 여행", places=[an.PlaceInfo(name="  동피랑  ", kind="관광지", paragraphs=[0, 9], photos=[0, 7],
                                                  evidence="글에 나옴"),
                                     an.PlaceInfo(name="", kind="맛집")],
        photos=[an.PhotoInfo(index=0, category="풍경", paragraph=0), an.PhotoInfo(index=5, category="음식"),
                an.PhotoInfo(index=2, category="이상한값", paragraph=40)])
    c = FakeClaude(_wire(answer))
    a = an.analyze(_post(), "k", client=c)
    assert c.calls[0]["model"] == "claude-sonnet-5-5" and c.calls[0]["output_format"] is an.AnalysisDraft
    assert [p.name for p in a.places] == ["동피랑"] and a.places[0].paragraphs == [0] and a.places[0].photos == [0]
    assert [p.index for p in a.photos] == [0, 1, 2]                         # 범위 밖은 빼고, 빠진 사진은 채움
    assert a.photos[1].paragraph == 1 and a.photos[2].category == "기타" and a.photos[2].paragraph == 2


def test_unknown_values_are_normalized():
    p = an.PlaceInfo(name="x", kind="놀이공원", evidence="확실함")
    assert (p.kind, p.evidence) == ("기타", "추론")


def test_brief_lists_places_with_evidence():
    a = an.TravelAnalysis(theme="통영 여행", places=[an.PlaceInfo(name="동피랑", evidence="추론")],
                          missing_info=["날짜가 글에 없음"])
    b = an.brief(a.model_dump())
    assert "장소 동피랑" in b and "추론" in b and "날짜가 글에 없음" in b
    assert an.brief(None) == ""


# ---------- 대본 설정 ----------
def test_settings_defaults_and_validation():
    s = ScriptSettings()
    assert (s.style, s.length, s.tone, s.info_ratio, s.detail) == ("vlog", "auto", "friendly", 50, "normal")
    for bad in ({"style": "news"}, {"length": "7"}, {"info_ratio": 120}):
        with pytest.raises(Exception):
            ScriptSettings(**bad)


@pytest.mark.parametrize("style,word", [("cinematic", "다큐멘터리"), ("vlog", "브이로그"), ("info", "정보 전달"),
                                        ("essay", "에세이"), ("review", "리뷰")])
def test_each_style_has_its_own_guide(style, word):
    assert word in writing_brief(ScriptSettings(style=style))


def test_brief_turns_settings_into_instructions():
    b = writing_brief(ScriptSettings(length="3", tone="polite", info_ratio=80, detail="detailed"), voice="male")
    assert "약 3분" in b and "990자" in b and "합쇼체" in b and "80 : 20" in b and "정보 전달 위주" in b
    assert "자세히" in b and "남성 성우" in b
    assert "글 전체" in writing_brief(ScriptSettings(length="auto")) and "여성 성우" in writing_brief()
    assert "감성 스토리텔링 위주" in writing_brief(ScriptSettings(info_ratio=20))


def test_prompt_has_story_structure_and_sections():
    for w in ("opening", "intro", "body", "place", "review", "ending", "후킹"):
        assert w in SYSTEM


def test_script_gets_settings_analysis_and_keeps_sections():
    draft = ScriptDraft(scenes=[SceneDraft(text="이 바다, 보이세요?", image_index=0, section="opening"),
                                SceneDraft(text="동피랑이에요.", image_index=1, section="place", place="동피랑"),
                                SceneDraft(text="끝", image_index=2, section="이상한값")],
                        yt_title="t", yt_description="d", yt_tags=[])
    c = FakeClaude(draft)
    a = an.TravelAnalysis(theme="통영 바다 여행", places=[an.PlaceInfo(name="동피랑")])
    s = generate_script(_post(), "k", client=c, analysis=a.model_dump(), settings=ScriptSettings(style="cinematic"))
    text = c.calls[0]["messages"][0]["content"]
    text = text if isinstance(text, str) else text[0]["text"]
    assert "[대본 설정]" in text and "다큐멘터리" in text and "통영 바다 여행" in text and "사진 1=문단 2 뒤" in text
    assert [x.section for x in s.scenes] == ["opening", "place", ""]


def test_old_call_without_settings_sends_no_brief():
    draft = ScriptDraft(scenes=[SceneDraft(text="a", image_index=0)], yt_title="t", yt_description="d", yt_tags=[])
    c = FakeClaude(draft)
    generate_script(_post(), "k", client=c)
    text = c.calls[0]["messages"][0]["content"]
    assert "[대본 설정]" not in (text if isinstance(text, str) else text[0]["text"])


# ---------- 작업 흐름: 불러오기 → 분석 → 대본 ----------
@pytest.fixture
def api(tmp_path):
    steps = fake_steps()
    seen = {"analyze": 0, "write": []}

    def analyze(post, key):
        seen["analyze"] += 1
        return an.TravelAnalysis(theme="통영 여행", places=[an.PlaceInfo(name="동피랑")])

    def write(post, key, analysis, settings, voice):
        seen["write"].append((analysis["theme"], settings.style, settings.length, voice))
        return steps.generate_script(post, key)

    steps.analyze, steps.write_script = analyze, write
    manager = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(manager), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    yield c, manager, seen
    manager.stop()


def test_job_analyzes_then_writes_with_settings(api):
    c, manager, seen = api
    job = c.post("/api/jobs", json={"url": URL, "mode": "api", "settings": {"style": "essay", "length": "5"},
                                    "options": {"voice": "male"}}).json()
    s = wait_stage(manager, job["id"], "ready")
    assert seen["analyze"] == 1 and seen["write"] == [("통영 여행", "essay", "5", "male")]
    j = c.get(f"/api/jobs/{job['id']}").json()
    assert j["analysis"]["theme"] == "통영 여행" and j["settings"]["style"] == "essay" and s.script.scenes


def test_rewrite_reuses_analysis_unless_asked(api):
    c, manager, seen = api
    job = c.post("/api/jobs", json={"url": URL, "mode": "api"}).json()
    wait_stage(manager, job["id"], "ready")
    c.post(f"/api/jobs/{job['id']}/write", json={"mode": "api", "settings": {"style": "info"}})
    wait_stage(manager, job["id"], "ready")
    assert seen["analyze"] == 1 and seen["write"][-1][1] == "info"      # 분석은 다시 하지 않음
    c.post(f"/api/jobs/{job['id']}/write", json={"mode": "api", "reanalyze": True})
    wait_stage(manager, job["id"], "ready")
    assert seen["analyze"] == 2


def test_stop_during_analysis_keeps_post(api):
    import threading

    c, manager, seen = api
    gate = threading.Event()
    real = manager.steps.analyze
    manager.steps.analyze = lambda post, key: (gate.wait(5), real(post, key))[1]
    job = c.post("/api/jobs", json={"url": URL, "mode": "api"}).json()
    wait_stage(manager, job["id"], "analyzing")
    c.post(f"/api/jobs/{job['id']}/cancel")
    gate.set()
    s = wait_stage(manager, job["id"], "loaded")
    assert s.post is not None and s.analysis is None and s.script is None


def test_bad_settings_rejected(api):
    c, _, _ = api
    assert c.post("/api/jobs", json={"url": URL, "settings": {"style": "news"}}).status_code == 400
