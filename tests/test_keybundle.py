"""지인용 설치 파일(키 포함) 만들기."""
import pytest
from fastapi.testclient import TestClient

from app import config, keybundle
from app.errors import UserError
from app.jobs import JobManager
from app.main import create_app
from tests.fakes import fake_steps

FAKE_SETUP = b"MZ" + b"\x00" * 100 + "BlogVideo".encode("utf-16-le") + b"\x90" * 5000


def test_encode_decode_roundtrip():
    enc = keybundle.encode_key("sk-ant-api03-ABC")
    assert "sk-ant" not in enc and keybundle.decode_key(enc) == "sk-ant-api03-ABC"


def test_embed_appends_fixed_trailer():
    out = keybundle.embed(FAKE_SETUP, "sk-ant-xyz")
    assert out.startswith(FAKE_SETUP)
    assert len(out) == len(FAKE_SETUP) + keybundle.TRAILER_SIZE
    assert out[-keybundle.TRAILER_SIZE:].startswith(b"BVKEY1")
    assert b"sk-ant-xyz" not in out
    assert keybundle.read_trailer(out) == {"anthropic_api_key": "sk-ant-xyz"}


def test_reembed_replaces_old_key():
    once = keybundle.embed(FAKE_SETUP, "sk-ant-old")
    twice = keybundle.embed(once, "sk-ant-new")
    assert len(twice) == len(once) and keybundle.read_trailer(twice) == {"anthropic_api_key": "sk-ant-new"}


@pytest.mark.parametrize("data", [b"not an exe", b"MZ" + b"\x00" * 100])
def test_embed_rejects_wrong_file(data):
    with pytest.raises(UserError, match="설치 파일"):
        keybundle.embed(data, "sk-ant-x")


def test_import_pending_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    pending = tmp_path / "config" / keybundle.IMPORT_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(keybundle.encode_key("sk-ant-imported1"), "ascii")
    assert keybundle.import_pending_key() is True
    assert config.load_config()["anthropic_api_key"] == "sk-ant-imported1"
    assert not pending.exists()
    assert keybundle.import_pending_key() is False


def test_import_ignores_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    pending = tmp_path / "config" / keybundle.IMPORT_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("zz-not-hex", "ascii")
    assert keybundle.import_pending_key() is False
    assert not pending.exists()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    m = JobManager(tmp_path / "jobs", fake_steps())
    yield TestClient(create_app(m), client=("127.0.0.1", 1), base_url="http://127.0.0.1:8765")
    m.stop()


def test_api_builds_keyed_installer(client):
    config.save_config({"anthropic_api_key": "sk-ant-mykey777"})
    r = client.post("/api/keyed-installer", files={"file": ("BlogVideo-Setup.exe", FAKE_SETUP, "application/octet-stream")})
    assert r.status_code == 200
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert keybundle.read_trailer(r.content) == {"anthropic_api_key": "sk-ant-mykey777"}


def test_api_keyed_installer_needs_key(client):
    r = client.post("/api/keyed-installer", files={"file": ("s.exe", FAKE_SETUP, "application/octet-stream")})
    assert r.status_code == 400 and "API 키" in r.json()["message"]


def test_api_keyed_installer_rejects_other_file(client):
    config.save_config({"anthropic_api_key": "sk-ant-mykey777"})
    r = client.post("/api/keyed-installer", files={"file": ("x.exe", b"hello", "application/octet-stream")})
    assert r.status_code == 400 and "설치 파일" in r.json()["message"]


def test_launcher_imports_key_on_start(tmp_path, monkeypatch):
    from app import launcher
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    pending = tmp_path / "config" / keybundle.IMPORT_FILE
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(keybundle.encode_key("sk-ant-fromsetup"), "ascii")
    from tests.test_launcher import free_port
    launcher.main(port=free_port(), opener=lambda u: None, serve=lambda *a: None, alert=pytest.fail)
    assert config.load_config()["anthropic_api_key"] == "sk-ant-fromsetup"
