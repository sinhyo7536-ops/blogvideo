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
    page.wait_for_selector(".scene-card", timeout=10000)
    page.click('#view-edit .seg[data-name="aspect"] button[data-value="9:16"]')
    shot(page, f"{tag}2_edit")
    page.fill(".scene-card textarea:not(.direction) >> nth=0", "고친 자막")
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


def _open(page, base):
    page.goto(base)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.fill("#url", "https://blog.naver.com/a/1")


def _to_edit(page, base):
    """링크를 넣고 'AI로 대본 만들기' → 대본 고치기 화면."""
    _open(page, base)
    page.click("#btn-load")
    page.wait_for_selector(".scene-card", timeout=10000)


def _to_manual(page, base):
    """'AI 없이 직접 작성하기' → 장면이 빈 직접 작성 화면."""
    _open(page, base)
    page.click("#btn-home-manual")
    page.wait_for_selector("#edit-title:has-text('직접 작성')", timeout=10000)


def _to_source(page, base):
    """대본 고치기에서 '작성 방법 다시 고르기' → 불러온 글 화면."""
    _to_edit(page, base)
    page.click("#btn-rewrite")
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
    page.fill(".scene-card textarea:not(.direction) >> nth=0", "바뀐 자막")
    page.click("#open-settings")
    page.wait_for_selector("#view-settings:not(.hidden)")
    page.click("#btn-settings-back")
    page.wait_for_selector("#view-edit:not(.hidden)")
    assert page.input_value(".scene-card textarea:not(.direction) >> nth=0") == "바뀐 자막"


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


def test_no_claude_paste_option(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(live_server)
    page.wait_for_selector("#btn-load")
    assert page.locator("text=붙여넣기").count() == 0


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
def test_loaded_post_shows_photos_text_and_two_choices(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_source(page, live_server)
    assert "테스트" in page.inner_text("#source-title")
    assert page.inner_text("#source-meta") == "사진 1장 · 글 1문단"
    assert page.locator("#source-photos .photo").count() == 1
    assert page.locator("#source-text p").count() == 1
    assert page.is_visible("#btn-recommend") and page.is_visible("#btn-manual")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_manual_writing_from_blog_text_and_photo(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_manual(page, live_server)
    assert page.inner_text("#edit-title") == "직접 작성"
    assert page.locator(".scene-card").count() == 0 and page.is_visible("#empty-scenes")
    assert page.evaluate("document.getElementById('source-panel').open")
    page.click("button:has-text('영상 만들기')")              # 장면 없이 만들 수 없음
    page.wait_for_selector("#edit-error:not(.hidden)")
    page.click("#panel-text .para >> nth=0")                  # 글 한 토막 → 장면
    page.click("#panel-photos .photo >> nth=0")               # 사진 → 장면
    page.click("#btn-add-scene")                              # 빈 장면
    assert page.locator(".scene-card").count() == 3 and not page.is_visible("#empty-scenes")
    assert page.input_value(".scene-card textarea:not(.direction) >> nth=0") == "p"
    page.fill(".scene-card textarea:not(.direction) >> nth=1", "내가 직접 쓴 자막")
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
    page.fill(".scene-card textarea:not(.direction) >> nth=1", "가" * 330)          # 약 1분 분량
    assert page.inner_text("#scene-summary").startswith("장면 2개 · 예상 길이 약 1분")
    page.on("dialog", lambda d: d.accept())
    page.click(".scene-card >> nth=1 >> button[title='삭제']")
    assert page.inner_text("#scene-summary") == first


def test_own_mp3_file_becomes_background_music(live_server, browser, tmp_path):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _to_edit(page, live_server)
    page.click('.seg[data-name="bgm-source"] button[data-value="file"]')
    assert page.is_visible("#bgm-upload") and not page.is_visible("#bgm-pick") and not page.is_visible("#yt-url")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    f = tmp_path / "내 여행 음악.mp3"
    f.write_bytes(b"ID3 fake mp3")
    page.set_input_files("#bgm-upload", str(f))
    page.wait_for_selector("#bgm-ok:not(.hidden)", timeout=5000)
    assert page.input_value("#bgm-edit") == "내 여행 음악.mp3" and page.is_visible("#bgm-pick")
    bad = tmp_path / "노래.wav"
    bad.write_bytes(b"RIFF")
    page.click('.seg[data-name="bgm-source"] button[data-value="file"]')
    page.set_input_files("#bgm-upload", str(bad))
    page.wait_for_selector("#bgm-error:has-text('MP3')")


# ---------- 사진 잘 보이게, 만드는 중 멈추기, 장면 자유롭게 추가 ----------
def _serve_with_photos(tmp_path, monkeypatch, n=7):
    """사진 n장(크기·비율이 제각각)이 있는 글."""
    from PIL import Image

    from app.models import BlogPost, ImageRef

    m = _start(tmp_path, monkeypatch)
    sizes = [(1600, 900), (900, 1600), (1200, 1200), (2000, 800), (800, 600), (640, 1136), (3000, 2000)]
    m.steps.fetch_post = lambda url: BlogPost(title="사진 많은 글", url=url,
                                              paragraphs=["첫 문단이에요. 둘째 문장이에요.", "둘째 문단"],
                                              images=[ImageRef(url=f"u{i}") for i in range(n)])

    def download(post, dest):
        dest.mkdir(parents=True, exist_ok=True)
        imgs = []
        for i in range(n):
            p = dest / f"img_{i:02d}.jpg"
            Image.new("RGB", sizes[i % len(sizes)], (30 * i % 255, 120, 200 - 20 * i)).save(p)
            imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
        return post.model_copy(update={"images": imgs})

    m.steps.download_images = download
    return m


BOXES_JS = """(sel) => [...document.querySelectorAll(sel)].map(e => {
    const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })"""
LOADED_JS = "(sel) => [...document.querySelectorAll(sel)].every(i => i.complete && i.naturalWidth)"


def _overlapping(boxes):
    bad = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1:
                bad.append((a, b))
    return bad


def _texts(page):
    return page.evaluate("state.script.scenes.map(s => s.text)")


@pytest.mark.parametrize("vp", [{"width": 390, "height": 844}, {"width": 1280, "height": 800}], ids=["mobile", "desktop"])
def test_photos_do_not_overlap_and_are_numbered(tmp_path, monkeypatch, browser, vp):
    for base in _serve(_serve_with_photos(tmp_path, monkeypatch)):
        page = browser.new_page(viewport=vp)
        _to_source(page, base)
        page.on("dialog", lambda d: d.accept())
        page.wait_for_function(LOADED_JS, arg="#source-photos img")
        boxes = page.evaluate(BOXES_JS, "#source-photos .photo")
        assert len(boxes) == 7 and not _overlapping(boxes)
        assert all(b[3] - b[1] > 60 for b in boxes)                      # 찌그러진 칸 없음
        assert page.locator("#source-photos .photo .num").all_inner_texts() == [str(i) for i in range(1, 8)]
        page.click("#btn-manual")
        page.wait_for_selector("#edit-title:has-text('직접 작성')")
        page.click("#panel-photos .photo >> nth=2")                       # 사진 3으로 장면 만들기
        page.wait_for_selector(".scene-card")
        assert not _overlapping(page.evaluate(BOXES_JS, "#panel-photos .photo"))
        assert page.inner_text("#panel-photos .photo >> nth=2 >> .used") == "사용 중"
        page.click(".scene-card .thumb >> nth=0")                         # 사진 고르기 창
        page.wait_for_selector("#picker[open]")
        page.wait_for_function(LOADED_JS, arg="#picker-grid img")
        boxes = page.evaluate(BOXES_JS, "#picker-grid .photo")
        assert len(boxes) == 8 and not _overlapping(boxes)                # 사진 없음 + 7장
        assert all(b[3] - b[1] > 60 for b in boxes)
        assert page.locator("#picker-grid .photo.selected .num").inner_text() == "3"
        page.click("#picker-grid .photo >> nth=5")                        # 사진 5로 바꾸기
        assert page.evaluate("state.script.scenes.map(s => s.image_index)") == [4]
        page.close()


def test_add_scene_anywhere_duplicate_and_split(tmp_path, monkeypatch, browser):
    for base in _serve(_serve_with_photos(tmp_path, monkeypatch)):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_manual(page, base)
        page.click("#btn-add-scene")
        page.fill(".scene-card textarea:not(.direction) >> nth=0", "A")
        page.click("#btn-add-scene")
        page.fill(".scene-card textarea:not(.direction) >> nth=1", "C")
        page.click(".scene-card >> nth=1 >> text=+ 앞에 추가")            # A와 C 사이에 끼우기
        page.keyboard.type("B")
        assert _texts(page) == ["A", "B", "C"]
        page.click(".scene-card >> nth=0 >> text=+ 앞에 추가")            # 맨 앞에
        page.keyboard.type("처음")
        assert _texts(page) == ["처음", "A", "B", "C"]
        page.click(".scene-card >> nth=3 >> text=+ 뒤에 추가")
        page.keyboard.type("끝")
        assert _texts(page) == ["처음", "A", "B", "C", "끝"]
        # 고른 장면 바로 아래에 블로그 글이 들어감
        page.click(".scene-card >> nth=1 >> textarea")
        assert "장면 2" in page.inner_text("#panel-hint")
        page.click("#panel-text .para >> nth=1")
        assert _texts(page) == ["처음", "A", "둘째 문단", "B", "C", "끝"]
        # 복제
        page.select_option(".scene-card >> nth=3 >> select.motion", "pan_left")
        page.click(".scene-card >> nth=3 >> text=복제")
        assert _texts(page) == ["처음", "A", "둘째 문단", "B", "B", "C", "끝"]
        assert page.input_value(".scene-card >> nth=4 >> select.motion") == "pan_left"
        # 나누기: 커서 위치에서
        page.fill(".scene-card textarea:not(.direction) >> nth=5", "앞부분 뒷부분")
        page.evaluate("document.querySelectorAll('.scene-card textarea:not(.direction)')[5].setSelectionRange(3, 3)")
        page.click(".scene-card >> nth=5 >> text=나누기")
        assert _texts(page)[5:7] == ["앞부분", "뒷부분"]
        # 나누기: 커서가 끝에 있으면 문장 끝에서
        page.fill(".scene-card textarea:not(.direction) >> nth=0", "첫 문장이에요. 둘째 문장이에요.")
        page.click(".scene-card >> nth=0 >> text=나누기")
        assert _texts(page)[:2] == ["첫 문장이에요.", "둘째 문장이에요."]
        # 나눌 수 없으면 안내만
        page.click(".scene-card >> nth=2 >> text=나누기")
        page.wait_for_selector("#toast:not(.hidden)")
        assert len(_texts(page)) == 9
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.close()


def test_stop_button_while_making_video(tmp_path, monkeypatch, browser):
    import threading

    m = _start(tmp_path, monkeypatch)
    gate = threading.Event()
    m.steps.abort = gate.set

    def slow(post, script, options, workdir, on_progress=None):
        on_progress(0.3)
        gate.wait(10)
        on_progress(0.6)

    real = m.steps.render_video
    m.steps.render_video = slow
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _to_edit(page, base)
        page.fill(".scene-card textarea:not(.direction) >> nth=0", "멈춰도 남아 있을 자막")
        page.click("button:has-text('영상 만들기')")
        page.wait_for_selector("#btn-cancel:has-text('영상 만들기 멈추기')", timeout=10000)
        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        page.click("#btn-cancel")
        page.wait_for_selector("#view-edit:not(.hidden)", timeout=10000)   # 대본 고치기로 돌아옴
        assert asked and "멈출까요" in asked[0]
        assert page.input_value(".scene-card textarea:not(.direction) >> nth=0") == "멈춰도 남아 있을 자막"
        m.steps.render_video = real                                        # 다시 만들면 끝까지 됨
        page.click("button:has-text('영상 만들기')")
        page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
        page.close()


def test_stop_button_while_loading_goes_home(tmp_path, monkeypatch, browser):
    m = _start(tmp_path, monkeypatch)
    m.steps.fetch_post = fake_steps(delay=1.5).fetch_post
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(base)
        page.fill("#url", "https://blog.naver.com/a/1")
        page.click("#btn-load")
        page.wait_for_selector("#btn-cancel:has-text('불러오기 멈추기')", timeout=5000)
        page.click("#btn-cancel")
        page.wait_for_selector("#view-home:not(.hidden)", timeout=10000)
        assert page.evaluate("localStorage.getItem('blogvideo.jobId')") is None
        page.close()


# ---------- 링크 → AI가 바로 대본, 연출 요청, 'AI 없이' 제거 ----------
def test_home_offers_two_ais_and_small_manual_button(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(live_server)
    page.wait_for_selector("#btn-load")
    assert page.locator('#view-home .seg[data-name="mode"]').count() == 0 and page.locator("text=제미나이").count() == 0
    assert page.inner_text("#btn-load") == "AI로 대본 만들기" and page.is_visible("#btn-home-manual")
    assert page.locator("text=AI 없이(무료)").count() == 0 and page.locator("#btn-free-auto").count() == 0
    assert "사진" in page.inner_text("#view-home .mode-help")
    box = page.locator("#btn-home-manual").bounding_box()
    big = page.locator("#btn-load").bounding_box()
    assert box["height"] < big["height"] and box["y"] > big["y"]          # 아래에 작게
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_link_then_ai_writes_without_extra_step(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _open(page, live_server)
    page.click("#btn-load")
    page.wait_for_selector(".scene-card", timeout=10000)                  # 고르는 화면 없이 바로 대본
    assert page.input_value("#yt-title") == "제목: 테스트/글" and _job(page)["mode"] == "api"


def test_api_error_has_no_free_fallback(live_server_api_fails, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _open(page, live_server_api_fails)
    page.click("#btn-load")
    page.wait_for_selector("#view-error:not(.hidden)", timeout=10000)
    assert page.is_visible("#btn-retry") and page.locator("text=AI 없이 계속하기").count() == 0


def test_direction_requests_are_sent_and_result_shown(tmp_path, monkeypatch, browser):
    from app.director import Direction, SceneDirection, apply

    m = _start(tmp_path, monkeypatch)
    seen = []

    def fake_direct(script, options, key):
        seen.append({"overall": script.direction, "scene": script.scenes[0].direction, "key": key})
        return apply(script, options, Direction(
            scenes=[SceneDirection(index=0, text="AI가 짧게 고친 자막", look="warm", caption_size="large", hold=2,
                                   motion="zoom_out", strength="subtle")], template="mood"))

    m.steps.direct = fake_direct
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _to_edit(page, base)
        assert page.is_visible("#direction") and page.locator(".scene-card textarea.direction").count() == 1
        assert not page.is_visible(".scene-card .applied")
        page.fill("#direction", "전체적으로 잔잔하게")
        assert not page.is_visible(".scene-card textarea.direction")      # 장면 연출 요청은 접혀 있음
        page.click(".scene-card .direction-more summary >> nth=0")
        page.fill(".scene-card textarea.direction >> nth=0", "따뜻하게, 자막 크게, 오래 보여 줘. 더 짧게 써 줘")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.click("button:has-text('영상 만들기')")
        page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
        assert seen == [{"overall": "전체적으로 잔잔하게", "scene": "따뜻하게, 자막 크게, 오래 보여 줘. 더 짧게 써 줘",
                         "key": "sk-ant-test1234"}]
        page.click("#btn-reedit")                                          # AI가 정한 연출이 화면에 보임
        page.wait_for_selector("#view-edit:not(.hidden)")
        assert page.input_value(".scene-card textarea:not(.direction) >> nth=0") == "AI가 짧게 고친 자막"
        assert page.input_value(".scene-card select.motion >> nth=0") == "zoom_out"
        assert page.inner_text(".scene-card .applied span") == "AI 연출: 따뜻한 색감 · 은은한 움직임 · 자막 크게 · 2초 더 머무름"
        assert page.locator('#view-edit .seg[data-name="template"] button.on').inner_text() == "감성"
        assert page.input_value("#direction") == "전체적으로 잔잔하게"
        page.click("button:has-text('영상 만들기')")                       # 요청을 안 바꾸면 AI를 다시 부르지 않음
        page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
        assert len(seen) == 1
        page.click("#btn-reedit")
        page.wait_for_selector("#view-edit:not(.hidden)")
        page.click(".scene-card .applied button")                          # 되돌리기
        assert not page.is_visible(".scene-card .applied")
        assert page.evaluate("state.script.scenes[0].look") == "auto"
        page.close()


def test_duplicate_keeps_direction_and_new_scene_has_empty_one(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _to_edit(page, live_server)
    page.click(".scene-card .direction-more summary >> nth=0")
    page.fill(".scene-card textarea.direction >> nth=0", "흑백으로")
    page.click(".scene-card >> nth=0 >> text=복제")
    page.click("#btn-add-scene")
    assert page.evaluate("state.script.scenes.map(s => s.direction || '')") == ["흑백으로", "흑백으로", ""]


# ---------- 5단계: 대본 편집기 ----------
TEXT = ".scene-card textarea:not(.direction)"


def _editor_server(tmp_path, monkeypatch, calls):
    from app.models import Scene, VideoScript

    m = _start(tmp_path, monkeypatch)
    m.steps.generate_script = lambda post, key: VideoScript(
        scenes=[Scene(text="첫 장면이에요.", image_index=0), Scene(text="둘째 장면 이에요.", image_index=0),
                Scene(text="셋째 장면이에요.", image_index=0)], yt_title="t", yt_description="d", yt_tags=[])

    def rewrite(script, indexes, command, key, post, analysis):
        calls.append((indexes, command))
        return [{"index": i, "before": script.scenes[i].text, "after": "AI가 고친 " + script.scenes[i].text}
                for i in indexes]

    def review(script, key):
        return [{"index": 1, "before": "장면 이에요", "after": "장면이에요", "reason": "띄어쓰기",
                 "text": "둘째 장면이에요."}]

    m.steps.rewrite, m.steps.review = rewrite, review
    return m


def test_ai_rewrite_one_scene_shows_diff_and_applies(tmp_path, monkeypatch, browser):
    calls = []
    m = _editor_server(tmp_path, monkeypatch, calls)
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _to_edit(page, base)
        page.click(".scene-card >> nth=1 >> button.ai-fix")
        page.click("#ai-presets .chip:has-text('30% 줄이기')")
        page.fill("#ai-command", "더 밝게")
        page.click("#btn-ai-run")
        page.wait_for_selector("#ai-results .suggestion")
        assert calls[0][0] == [1] and "30%" in calls[0][1] and "더 밝게" in calls[0][1]
        assert page.inner_text("#ai-results ins").strip() == "AI가 고친"
        page.click("#btn-ai-close")                                          # 닫으면 그대로
        assert page.input_value(f"{TEXT} >> nth=1") == "둘째 장면 이에요."
        page.click(".scene-card >> nth=1 >> button.ai-fix")
        page.click("#ai-presets .chip >> nth=0")
        page.click("#btn-ai-run")
        page.click("#btn-ai-apply")
        assert page.input_value(f"{TEXT} >> nth=1") == "AI가 고친 둘째 장면 이에요."
        assert page.input_value(f"{TEXT} >> nth=0") == "첫 장면이에요."      # 다른 장면은 그대로
        page.wait_for_function("document.getElementById('save-status').textContent.includes('저장됨')")
        job_id = page.evaluate("state.job.id")
        assert m.get(job_id).script.scenes[1].text == "AI가 고친 둘째 장면 이에요."
        assert [v["label"] for v in m.versions(job_id)][0] == "AI로 고치기 전"
        page.close()


def test_review_fixes_only_the_wrong_part(tmp_path, monkeypatch, browser):
    m = _editor_server(tmp_path, monkeypatch, [])
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_edit(page, base)
        page.fill(f"{TEXT} >> nth=1", "둘째 장면 이에요. 내가 더 씀")             # 검사 전에 고친 글도 유지
        page.click("#btn-review")
        page.wait_for_selector("#ai-results .suggestion")
        assert "띄어쓰기" in page.inner_text("#ai-results")
        page.click("#btn-ai-apply")
        assert page.input_value(f"{TEXT} >> nth=1") == "둘째 장면이에요. 내가 더 씀"
        page.close()


def test_autosave_versions_and_restore(tmp_path, monkeypatch, browser):
    m = _editor_server(tmp_path, monkeypatch, [])
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_edit(page, base)
        job_id = page.evaluate("state.job.id")
        page.fill(f"{TEXT} >> nth=0", "손으로 고친 첫 장면")
        page.wait_for_function("document.getElementById('save-status').textContent.includes('저장됨')")
        assert m.get(job_id).script.scenes[0].text == "손으로 고친 첫 장면"   # 버튼을 안 눌러도 저장
        page.reload()                                                       # 껐다 켜도 남아 있음
        page.wait_for_selector(".scene-card")
        assert page.input_value(f"{TEXT} >> nth=0") == "손으로 고친 첫 장면"
        page.click("#btn-versions")
        page.wait_for_selector("#versions-list .version")
        assert "AI가 쓴 대본" in page.inner_text("#versions-list")
        page.once("dialog", lambda d: d.accept())
        page.click("#versions-list .version >> nth=-1 >> button")           # 가장 오래된 = AI가 쓴 대본
        page.wait_for_function("document.querySelector('.scene-card textarea').value === '첫 장면이에요.'")
        assert m.get(job_id).script.scenes[0].text == "첫 장면이에요."
        assert m.versions(job_id)[0]["label"] == "되돌리기 전"
        page.close()


def test_overview_lists_every_scene(tmp_path, monkeypatch, browser):
    m = _editor_server(tmp_path, monkeypatch, [])
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _to_edit(page, base)
        page.click("#btn-overview")
        assert page.locator("#overview-list li").count() == 3
        assert "셋째 장면이에요." in page.inner_text("#overview-list")
        page.click("#overview-list li >> nth=2 >> button")
        assert not page.is_visible("#overview-dialog")
        page.close()


def test_confirm_flow_before_render(tmp_path, monkeypatch, browser):
    m = _editor_server(tmp_path, monkeypatch, [])
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_edit(page, base)
        assert page.inner_text("#btn-render") == "대본 확정하고 영상 만들기"
        page.click("#btn-confirm")
        page.wait_for_selector("#confirm-box.done")
        assert page.inner_text("#btn-render") == "영상 만들기"
        page.fill(f"{TEXT} >> nth=0", "확정 뒤에 고침")                       # 고치면 다시 확정해야 함
        assert not page.is_visible("#confirm-box.done")
        assert page.inner_text("#btn-render") == "대본 확정하고 영상 만들기"
        page.click("#btn-render")
        page.wait_for_selector("#view-result:not(.hidden)", timeout=10000)
        job_id = page.evaluate("state.job.id")
        assert m.get(job_id).script.scenes[0].text == "확정 뒤에 고침" and m.is_confirmed(job_id)
        page.close()


# ---------- 2단계: AI 모델 설정 화면 ----------
def test_model_settings_screen(live_server, browser, monkeypatch):
    from app.ai import models as ai

    monkeypatch.setattr(ai, "check", lambda key: [
        {"model": "claude-opus-5-5", "label": "Opus 5.5 · 가장 좋은 품질", "tasks": ["여행 대본 쓰기"], "ok": True,
         "message": "사용할 수 있어요"},
        {"model": "claude-haiku-5-5", "label": "Haiku 5.5 · 빠르고 아주 저렴", "tasks": ["맞춤법·영상 검수"], "ok": False,
         "message": "이 키로는 이 모델을 쓸 수 없어요. 다른 모델을 골라 주세요."}])
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(live_server)
    page.click("#open-settings")
    page.wait_for_selector("#model-list select")
    assert page.locator("#model-list select").count() == 6
    assert page.input_value('#model-list select[data-task="script"]') == "claude-opus-5-5"
    assert page.input_value('#model-list select[data-task="review"]') == "claude-haiku-5-5"
    assert "추천" in page.inner_text('#model-list select[data-task="photos"] option[value="claude-sonnet-5-5"]')
    assert page.locator("text=제미나이").count() == 0 and page.locator("#gemini-key").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.select_option('#model-list select[data-task="photos"]', "claude-opus-5-5")
    page.click("#btn-save-models")
    page.wait_for_selector("#models-ok:not(.hidden)")
    page.click("#btn-check-models")
    page.wait_for_selector("#model-check li >> nth=1")
    items = page.locator("#model-check li").all_inner_texts()
    assert items[0].startswith("✓ Opus 5.5") and items[1].startswith("✕ Haiku 5.5") and "다른 모델" in items[1]
    shot(page, "m9_models")
    page.reload()
    page.click("#open-settings")
    page.wait_for_selector("#model-list select")
    assert page.input_value('#model-list select[data-task="photos"]') == "claude-opus-5-5"


# ---------- 4단계: 대본 설정, 여행 분석 보기 ----------
def test_script_settings_sent_and_remembered(live_server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _open(page, live_server)
    page.click('.seg[data-name="sstyle"] button[data-value="essay"]')
    page.select_option("#s-length", "3")
    page.select_option("#s-tone", "calm")
    page.fill("#s-ratio", "20")
    page.dispatch_event("#s-ratio", "input")
    assert page.inner_text("#s-ratio-label") == "감성 위주"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.click("#btn-load")
    page.wait_for_selector(".scene-card", timeout=10000)
    st = _job(page)["settings"]
    assert (st["style"], st["length"], st["tone"], st["info_ratio"]) == ("essay", "3", "calm", 20)
    page.on("dialog", lambda d: d.accept())
    page.click("#go-home")
    page.wait_for_selector("#view-home:not(.hidden)")
    page.reload()
    page.wait_for_selector("#btn-load")
    assert page.locator('.seg[data-name="sstyle"] button.on').get_attribute("data-value") == "essay"
    assert page.input_value("#s-length") == "3"


def test_analysis_panel_and_photo_notice(tmp_path, monkeypatch, browser):
    from app.ai.analyze import Note, PlaceInfo, TravelAnalysis

    m = _start(tmp_path, monkeypatch)
    m.steps.analyze = lambda post, key: TravelAnalysis(
        theme="통영 바다 여행", period="10월", places=[PlaceInfo(name="동피랑", kind="관광지", evidence="글에 나옴",
                                                             experience="벽화를 구경했어요"),
                                                   PlaceInfo(name="강구안", kind="자연", evidence="추론")],
        episodes=[Note(text="길을 헤맸어요", evidence="글에 나옴")], missing_info=["숙소 이름이 글에 없음"])
    real_dl = m.steps.download_images
    m.steps.download_images = lambda post, dest: real_dl(post, dest).model_copy(
        update={"missing": ["2번째 사진"], "duplicates": 1})
    for base in _serve(m):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _to_edit(page, base)
        assert page.is_visible("#analysis-panel")
        page.click("#analysis-panel summary")
        body = page.inner_text("#analysis-body")
        assert "통영 바다 여행" in body and "동피랑" in body and "벽화를 구경했어요" in body and "숙소 이름이 글에 없음" in body
        assert page.locator("#analysis-body .evidence.guess").count() == 1
        notice = page.inner_text("#photo-notice")
        assert "1장을 받지 못했어요" in notice and "2번째 사진" in notice and "같은 사진 1장" in notice
        page.close()
