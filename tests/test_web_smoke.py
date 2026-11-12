import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.config import load_config, save_config
from app.jobs import JobManager
from app.main import create_app
from tests.fakes import fake_steps

pytestmark = pytest.mark.e2e
pw = pytest.importorskip("playwright.sync_api")

CHROMIUM = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
SHOTS = Path(os.environ.get("SHOT_DIR", "")) if os.environ.get("SHOT_DIR") else None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _start(tmp_path, monkeypatch, api_fails=False):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    save_config({"anthropic_api_key": "sk-ant-test1234"})
    steps = fake_steps(delay=0.2)
    steps.api_key = lambda: load_config().get("anthropic_api_key", "")
    if api_fails:
        from app.errors import UserError

        def broke(post, key):
            raise UserError("API 크레딧이 부족해요. Anthropic 콘솔에서 결제 상태를 확인해 주세요.")

        steps.generate_script = broke
    return JobManager(tmp_path / "jobs", steps)


@pytest.fixture
def live_server_api_fails(tmp_path, monkeypatch):
    yield from _serve(_start(tmp_path, monkeypatch, api_fails=True))


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    yield from _serve(_start(tmp_path, monkeypatch))


def _serve(m):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(m), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)
    m.stop()


@pytest.fixture
def browser():
    with pw.sync_playwright() as p:
        kw = {"executable_path": CHROMIUM} if Path(CHROMIUM).exists() else {}
        b = p.chromium.launch(**kw)
        yield b
        b.close()


def shot(page, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("vp", [{"width": 390, "height": 844}, {"width": 1280, "height": 800}], ids=["mobile", "desktop"])
def test_full_flow(live_server, browser, vp):
    page = browser.new_page(viewport=vp)
    tag = "m" if vp["width"] < 600 else "d"
    page.goto(live_server)
    page.wait_for_selector("text=블로그 글로 영상 만들기")
    shot(page, f"{tag}1_home")
    page.fill("#url", "https://blog.naver.com/a/1")
    page.click("#btn-load")
    page.wait_for_selector("#view-source:not(.hidden)", timeout=10000)
    shot(page, f"{tag}1b_source")
    page.click("#btn-recommend")
    page.wait_for_selector(".scene-card", timeout=10000)
    page.click('#view-edit .seg[data-name="aspect"] button[data-value="9:16"]')
    shot(page, f"{tag}2_edit")
    page.fill(".scene-card textarea >> nth=0", "고친 자막")
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("video", timeout=10000)
    shot(page, f"{tag}3_result")
    assert page.is_visible("text=영상 저장")
    assert page.is_visible("text=제목 복사")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # 새로고침해도 결과 화면 유지
    page.reload()
    page.wait_for_selector("video", timeout=5000)


def test_error_and_retry(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(live_server)
    page.fill("#url", "https://tistory.com/1")
    page.click("#btn-load")
    page.wait_for_selector("text=네이버 블로그 글 주소를 넣어 주세요.")


def test_settings_visible_on_pc(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(live_server)
    page.click("text=설정")
    page.wait_for_selector("#api-key")
    page.fill("#api-key", "sk-ant-newkey9999")
    page.click("button:has-text('저장')")
    page.wait_for_selector("text=저장했어요")
    shot(page, "d4_settings")


def _to_edit(page, base):
    page.goto(base)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.fill("#url", "https://blog.naver.com/a/1")
    page.click("#btn-load")
    page.wait_for_selector("#view-source:not(.hidden)", timeout=10000)
    page.click("#btn-recommend")
    page.wait_for_selector(".scene-card", timeout=10000)


def _to_source(page, base):
    page.goto(base)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.fill("#url", "https://blog.naver.com/a/1")
    page.click("#btn-load")
    page.wait_for_selector("#view-source:not(.hidden)", timeout=10000)


def test_home_button_leaves_edit(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    page.on("dialog", lambda d: d.accept())
    page.click("#go-home")
    page.wait_for_selector("#view-home:not(.hidden)", timeout=3000)
    assert page.evaluate("localStorage.getItem('blogvideo.jobId')") is None


def test_settings_roundtrip_keeps_edits(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.fill(".scene-card textarea >> nth=0", "바뀐 자막")
    page.click("#open-settings")
    page.wait_for_selector("#view-settings:not(.hidden)")
    page.click("#btn-settings-back")
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.input_value(".scene-card textarea >> nth=0") == "바뀐 자막"


def test_rerender_refreshes_preview(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    first = page.get_attribute("#video", "src")
    page.click("#btn-reedit")
    page.wait_for_selector("#view-edit:not(.hidden)")
    page.wait_for_timeout(50)
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-progress:not(.hidden)", timeout=5000)
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    assert page.get_attribute("#video", "src") != first


def test_make_keyed_installer(live_server, browser, tmp_path):
    from app import keybundle
    fake = tmp_path / "BlogVideo-Setup.exe"
    fake.write_bytes(b"MZ" + b"\x00" * 100 + "BlogVideo".encode("utf-16-le") + b"\x90" * 5000)
    page = browser.new_page(viewport={"width": 1280, "height": 800}, accept_downloads=True)
    page.goto(live_server)
    page.click("#open-settings")
    page.wait_for_selector("#setup-file")
    page.set_input_files("#setup-file", str(fake))
    with page.expect_download() as dl:
        page.click("#btn-keyed")
    d = dl.value
    assert "키포함" in d.suggested_filename and d.suggested_filename.endswith(".exe")
    assert keybundle.read_trailer(open(d.path(), "rb").read()) == {"anthropic_api_key": "sk-ant-test1234"}
    page.wait_for_selector("text=만들었어요")
    shot(page, "d5_keyed")


def _start_job(page, base, mode_label):
    page.goto(base)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.fill("#url", "https://blog.naver.com/a/1")
    page.click("#btn-load")
    page.wait_for_selector("#view-source:not(.hidden)", timeout=10000)
    page.click(f"#view-source >> text={mode_label}")
    page.click("#btn-recommend")


def test_free_auto_flow(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _start_job(page, live_server, "AI 없이(무료)")
    page.wait_for_selector(".scene-card", timeout=10000)
    assert "끝까지 봐 주셔서" in page.input_value(".scene-card textarea >> nth=-1")


def test_api_error_offers_free_modes(live_server_api_fails, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _start_job(page, live_server_api_fails, "Claude(유료)")
    page.wait_for_selector("#view-error:not(.hidden)", timeout=10000)
    shot(page, "m7_error_free")
    page.click("#btn-free-auto")
    page.wait_for_selector(".scene-card", timeout=5000)


def test_no_claude_paste_option(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_source(page, live_server)
    page.wait_for_selector("text=AI 없이(무료)")
    assert page.locator("text=붙여넣기").count() == 0


def test_gemini_key_and_mode_in_ui(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_source(page, live_server)
    page.wait_for_selector("text=Gemini(무료)")
    page.click("#open-settings")
    page.fill("#gemini-key", "AIzaSyUI-TEST-4321")
    page.click("#btn-save-gemini")
    page.wait_for_selector("text=지금 저장된 Gemini 키: AIzaSy…4321")
    shot(page, "d8_settings_gemini")


def test_quality_choice_is_sent(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    page.click('#view-edit .seg[data-name="quality"] button[data-value="1440p"]')
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    job = page.evaluate("fetch('/api/jobs/' + localStorage.getItem('blogvideo.jobId')).then(r => r.json())")
    assert job["options"]["quality"] == "1440p"
    page.click("#btn-reedit")   # 다시 고치러 가도 선택한 화질이 표시됨
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.locator('#view-edit .seg[data-name="quality"] button.on').inner_text() == "1440p"


def test_edit_options_fit_on_phone(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    box = page.locator('#view-edit .seg[data-name="quality"] button[data-value="1440p"]').bounding_box()
    assert box["x"] + box["width"] <= 390 - 16


def test_page_sends_heartbeat_and_bye(tmp_path, monkeypatch, browser):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    app = create_app(m)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}")
        page.wait_for_timeout(800)
        assert app.state.presence.last_seen is not None
        page.close(run_before_unload=True)  # 창 닫기
        deadline = time.time() + 3
        while app.state.presence.bye_at is None and time.time() < deadline:
            time.sleep(0.05)
        assert app.state.presence.bye_at is not None
    finally:
        server.should_exit = True
        t.join(5)
        m.stop()


# ---------- 여행 영상 기능 ----------
def _job(page):
    return page.evaluate("fetch('/api/jobs/' + localStorage.getItem('blogvideo.jobId')).then(r => r.json())")


def test_template_and_effects_are_sent(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.click('#view-edit .seg[data-name="template"] button[data-value="vlog"]')
    assert "형광펜" in page.inner_text("#template-help")
    page.click('#view-edit .seg[data-name="effects"] button[data-value="soft"]')
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    job = _job(page)
    assert (job["options"]["template"], job["options"]["effects"]) == ("vlog", "soft")


def test_place_intro_and_toggles_are_saved(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    assert page.is_checked("#opt-intro") and page.is_checked("#opt-outro") and page.is_checked("#opt-title-bar")
    page.fill(".scene-card input.place >> nth=0", "통영 동피랑")
    page.fill("#intro-title", "통영 1박 2일 여행")
    page.fill("#intro-sub", "가을 · 친구와")
    page.uncheck("#opt-outro")
    page.click('#view-edit .seg[data-name="template"] button[data-value="mood"]')
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    job = _job(page)
    assert job["script"]["scenes"][0]["place"] == "통영 동피랑"
    assert (job["script"]["intro_title"], job["script"]["intro_sub"]) == ("통영 1박 2일 여행", "가을 · 친구와")
    assert (job["options"]["intro"], job["options"]["outro"], job["options"]["template"]) == (True, False, "mood")
    # 다시 고치러 가면 입력한 값이 그대로
    page.click("#btn-reedit")
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.input_value(".scene-card input.place >> nth=0") == "통영 동피랑"
    assert not page.is_checked("#opt-outro")


def test_thumbnail_shown_when_made(tmp_path, monkeypatch, browser):
    from PIL import Image

    m = _start(tmp_path, monkeypatch)
    real = m.steps.render_video

    def render(post, script, options, workdir, on_progress=None):
        out = real(post, script, options, workdir, on_progress)
        Image.new("RGB", (1280, 720), "orange").save(workdir / "thumbnail.jpg")
        return out

    m.steps.render_video = render
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_edit(page, base)
        page.click("button:has-text('영상 만들기')")
        page.wait_for_selector("#thumb-box:not(.hidden)", timeout=10000)
        page.wait_for_function("document.getElementById('thumb-img').naturalWidth === 1280")
        assert "thumbnail?download=1" in page.get_attribute("#btn-thumb", "href")
        page.close()


def test_no_thumbnail_box_without_thumbnail(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    assert not page.is_visible("#thumb-box")


def test_channel_name_saved_in_settings(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(live_server)
    page.click("#open-settings")
    page.wait_for_selector("#channel-name")
    page.fill("#channel-name", "모두의 여행노트")
    page.click("#btn-save-channel")
    page.wait_for_selector("#channel-ok:not(.hidden)")
    assert load_config()["channel_name"] == "모두의 여행노트"
    assert load_config()["anthropic_api_key"] == "sk-ant-test1234"   # 키는 그대로
    page.reload()
    page.click("#open-settings")
    page.wait_for_function("document.getElementById('channel-name').value === '모두의 여행노트'")


# ---------- 기능 나누기: 불러오기 → 추천대로 / 직접 작성 ----------
def test_home_only_asks_for_the_address(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(live_server)
    page.wait_for_selector("#btn-load")
    assert page.locator("#view-home .seg").count() == 0
    assert page.inner_text("#btn-load") == "블로그 불러오기"


def test_loaded_post_shows_photos_text_and_two_choices(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_source(page, live_server)
    assert "테스트" in page.inner_text("#source-title")
    assert page.inner_text("#source-meta") == "사진 1장 · 글 1문단"
    assert page.locator("#source-photos .photo").count() == 1
    assert page.locator("#source-text p").count() == 1
    assert page.is_visible("#btn-recommend") and page.is_visible("#btn-manual")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert _job(page)["script"] is None         # 아직 대본을 만들지 않음
    page.reload()                                # 새로고침해도 이 화면
    page.wait_for_selector("#view-source:not(.hidden)", timeout=5000)


def test_manual_writing_from_blog_text_and_photo(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_source(page, live_server)
    page.click("#btn-manual")
    page.wait_for_selector("#view-edit:not(.hidden)", timeout=5000)
    assert page.inner_text("#edit-title") == "직접 작성"
    assert page.locator(".scene-card").count() == 0 and page.is_visible("#empty-scenes")
    assert page.evaluate("document.getElementById('source-panel').open")
    page.click("button:has-text('영상 만들기')")              # 장면 없이 만들 수 없음
    page.wait_for_selector("#edit-error:not(.hidden)")
    page.click("#panel-text .para >> nth=0")                  # 글 한 토막 → 장면
    page.click("#panel-photos .photo >> nth=0")               # 사진 → 장면
    page.click("#btn-add-scene")                              # 빈 장면
    assert page.locator(".scene-card").count() == 3 and not page.is_visible("#empty-scenes")
    assert page.input_value(".scene-card textarea >> nth=0") == "p"
    page.fill(".scene-card textarea >> nth=1", "내가 직접 쓴 자막")
    page.select_option(".scene-card select.motion >> nth=1", "pan_left")
    page.select_option(".scene-card select.transition >> nth=1", "circle")
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    job = _job(page)
    assert job["mode"] == "manual" and len(job["script"]["scenes"]) == 2      # 빈 장면은 빠짐
    second = job["script"]["scenes"][1]
    assert (second["text"], second["motion"], second["transition"]) == ("내가 직접 쓴 자막", "pan_left", "circle")
    page.click("#btn-reedit")
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.input_value(".scene-card select.motion >> nth=1") == "pan_left"


def test_recommended_scenes_have_effect_pickers(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    assert page.inner_text("#edit-title") == "대본 고치기"
    assert not page.evaluate("document.getElementById('source-panel').open")
    assert page.input_value(".scene-card select.motion >> nth=0") == "auto"
    assert page.locator(".scene-card select.transition option").count() == 8
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_choose_writing_method_again(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.click("#btn-rewrite")
    page.wait_for_selector("#view-source:not(.hidden)")
    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    page.click("#btn-manual")
    page.wait_for_selector("#edit-title:has-text('직접 작성')", timeout=5000)
    assert seen and "새로" in seen[0]
    assert page.locator(".scene-card").count() == 0


# ---------- 목소리 넓히기, 유튜브 링크 배경음악, 예상 길이 ----------
def test_voice_choice_speed_and_preview(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    assert page.locator("#voice option").count() >= 12
    assert page.locator("#voice optgroup").count() == 2
    page.select_option("#voice", "hyunsu")
    page.click('#view-edit .seg[data-name="speed"] button[data-value="fast"]')
    with page.expect_response("**/api/voice-preview") as resp:
        page.click("#btn-voice-play")
    assert resp.value.status == 200 and resp.value.request.post_data_json == {"voice": "hyunsu", "speed": "fast"}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    job = _job(page)
    assert (job["options"]["voice"], job["options"]["speed"]) == ("hyunsu", "fast")
    page.click("#btn-reedit")
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.input_value("#voice") == "hyunsu"
    assert page.locator('#view-edit .seg[data-name="speed"] button.on').get_attribute("data-value") == "fast"


def test_youtube_link_becomes_background_music(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    assert page.is_visible("#bgm-pick") and not page.is_visible("#bgm-youtube")
    assert page.locator("#bgm-edit option").count() >= 4          # 없음 + 샘플 3곡
    page.click('.seg[data-name="bgm-source"] button[data-value="youtube"]')
    assert page.is_visible("#yt-url") and not page.is_visible("#bgm-pick")
    assert "허락" in page.inner_text("#bgm-youtube .warn-box")
    page.click("#btn-yt-music")                                    # 빈 칸
    page.wait_for_selector("#bgm-error:not(.hidden)")
    page.fill("#yt-url", "https://example.com/not-youtube")
    page.click("#btn-yt-music")
    page.wait_for_selector("#bgm-error:has-text('유튜브 영상 주소')")
    page.fill("#yt-url", "https://www.youtube.com/watch?v=abc")
    page.click("#btn-yt-music")
    page.wait_for_selector("#bgm-ok:not(.hidden)", timeout=5000)
    assert page.input_value("#bgm-edit") == "유튜브 곡.mp3" and page.is_visible("#bgm-pick")
    page.click("button:has-text('영상 만들기')")
    page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
    assert _job(page)["options"]["bgm"] == "유튜브 곡.mp3"


def test_sample_music_can_be_chosen_and_previewed(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.click("#btn-bgm-play")                                    # 아무것도 안 골랐을 때
    page.wait_for_selector("#toast:not(.hidden)")
    name = page.locator("#bgm-edit option >> nth=1").get_attribute("value")
    page.select_option("#bgm-edit", name)
    with page.expect_response("**/api/music/file/**") as resp:
        page.click("#btn-bgm-play")
    assert resp.value.status in (200, 206)


def test_scene_summary_shows_count_and_length(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    first = page.inner_text("#scene-summary")
    assert first.startswith("장면 1개 · 예상 길이 약")
    page.click("#btn-add-scene")
    page.fill(".scene-card textarea >> nth=1", "가" * 330)          # 약 1분 분량
    assert page.inner_text("#scene-summary").startswith("장면 2개 · 예상 길이 약 1분")
    page.on("dialog", lambda d: d.accept())
    page.click(".scene-card >> nth=1 >> button[title='삭제']")
    assert page.inner_text("#scene-summary") == first
