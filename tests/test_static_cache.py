"""앱 창이 예전 화면 코드(app.js)를 캐시에 남겨 쓰지 않게 (2026-11-15: 0.6.0 화면이 남아 'gemini'를 보내 400)."""
import re

from fastapi.testclient import TestClient

from app.jobs import JobManager
from app.main import create_app
from tests.fakes import fake_steps


def _client(tmp_path):
    m = JobManager(tmp_path / "jobs", fake_steps())
    return m, TestClient(create_app(m), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))


def test_page_links_static_files_by_content_fingerprint(tmp_path):
    m, c = _client(tmp_path)
    html = c.get("/").text
    js = re.search(r'src="/static/app\.js\?v=([0-9a-f]{8,})"', html)
    css = re.search(r'href="/static/app\.css\?v=([0-9a-f]{8,})"', html)
    assert js and css
    assert c.get(f"/static/app.js?v={js.group(1)}").status_code == 200
    m.stop()


def test_static_files_are_always_revalidated(tmp_path):
    m, c = _client(tmp_path)
    for path in ("/static/app.js", "/static/app.css", "/"):
        assert "no-cache" in c.get(path).headers.get("cache-control", ""), path
    m.stop()
