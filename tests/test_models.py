from app.models import RenderOptions, Scene
from app.paths import jobs_dir, config_dir


def test_render_options_defaults():
    o = RenderOptions()
    assert (o.aspect, o.voice, o.bgm, o.bgm_volume) == ("16:9", "female", None, 0.15)


def test_scene_without_image_allowed():
    assert Scene(text="안녕", image_index=None).image_index is None


def test_paths_respect_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "config" and config_dir().is_dir()
    assert jobs_dir() == tmp_path / "jobs" and jobs_dir().is_dir()
