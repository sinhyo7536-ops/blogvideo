from app.config import load_config, save_config


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    assert load_config() == {}
    save_config({"anthropic_api_key": "sk-test"})
    assert load_config()["anthropic_api_key"] == "sk-test"


def test_config_keeps_korean(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    save_config({"note": "한글"})
    assert load_config()["note"] == "한글"
