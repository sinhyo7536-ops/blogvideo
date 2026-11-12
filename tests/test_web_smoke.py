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
    page.click("text=세로 영상(쇼츠)")
    page.click("button:has-text('대본 만들기')")
    page.wait_for_selector(".scene-card", timeout=10000)
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
    page.click("button:has-text('대본 만들기')")
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
    page.click("button:has-text('대본 만들기')")
    page.wait_for_selector(".scene-card", timeout=10000)


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
    page.click(f"text={mode_label}")
    page.click("#btn-script")


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
    page.goto(live_server)
    page.wait_for_selector("text=AI 없이(무료)")
    assert page.locator("text=붙여넣기").count() == 0


def test_gemini_key_and_mode_in_ui(live_server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(live_server)
    page.wait_for_selector("text=Gemini(무료)")
    page.click("#open-settings")
    page.fill("#gemini-key", "AIzaSyUI-TEST-4321")
    page.click("#btn-save-gemini")
    page.wait_for_selector("text=지금 저장된 Gemini 키: AIzaSy…4321")
    shot(page, "d8_settings_gemini")
