import os
import time
from pathlib import Path

import pytest

from app.jobs import JobManager
from app.models import RenderOptions
from tests.fakes import fake_steps, wait_stage


@pytest.fixture
def manager(tmp_path):
    m = JobManager(tmp_path, fake_steps())
    yield m
    m.stop()


def test_job_flow_to_done(manager):
    j = manager.create("https://blog.naver.com/a/1", RenderOptions())
    s = wait_stage(manager, j.id, "ready")
    assert s.script.scenes[0].text == "장면" and s.progress == 1.0
    manager.start_render(j.id)
    s = wait_stage(manager, j.id, "done")
    assert Path(s.video_path).exists()


def test_failed_step_sets_error_and_retry(tmp_path):
    m = JobManager(tmp_path, fake_steps(fail_fetch_times=1))
    j = m.create("u", RenderOptions())
    s = wait_stage(m, j.id, "error")
    assert s.message == "글을 읽을 수 없어요. 전체공개 글인지 확인해 주세요."
    m.retry(j.id)
    wait_stage(m, j.id, "ready")
    m.stop()


def test_unexpected_exception_generic_message(tmp_path):
    steps = fake_steps()
    steps.fetch_post = lambda url: 1 / 0
    m = JobManager(tmp_path, steps)
    j = m.create("u", RenderOptions())
    assert wait_stage(m, j.id, "error").message == "알 수 없는 오류가 났어요. 다시 시도해 주세요."
    m.stop()


def test_two_jobs_queue_sequentially(tmp_path):
    m = JobManager(tmp_path, fake_steps(delay=0.3))
    a = m.create("u1", RenderOptions())
    b = m.create("u2", RenderOptions())
    assert m.get(b.id).stage == "queued"
    wait_stage(m, b.id, "ready")
    assert m.get(a.id).stage == "ready"
    assert m.get(a.id).post.url == "u1" and m.get(b.id).post.url == "u2"
    m.stop()


def test_update_script_and_rerender(manager):
    j = manager.create("u", RenderOptions())
    s = wait_stage(manager, j.id, "ready")
    new = s.script.model_copy(update={"yt_title": "새 제목"})
    manager.update_script(j.id, new, RenderOptions(aspect="9:16"))
    assert manager.get(j.id).options.aspect == "9:16" and manager.get(j.id).script.yt_title == "새 제목"
    manager.start_render(j.id)
    wait_stage(manager, j.id, "done")


def test_update_script_rejected_while_busy(tmp_path):
    m = JobManager(tmp_path, fake_steps(delay=0.5))
    j = m.create("u", RenderOptions())
    from app.errors import UserError
    with pytest.raises(UserError):
        m.start_render(j.id)
    m.stop()


def test_state_survives_restart(tmp_path):
    m = JobManager(tmp_path, fake_steps())
    j = m.create("u", RenderOptions())
    wait_stage(m, j.id, "ready")
    m.stop()
    m2 = JobManager(tmp_path, fake_steps())
    assert m2.get(j.id).stage == "ready"
    m2.stop()


def test_interrupted_job_becomes_error_on_restart(tmp_path):
    m = JobManager(tmp_path, fake_steps(delay=1.0))
    j = m.create("u", RenderOptions())
    time.sleep(0.2)
    m.stop(wait=False)
    m2 = JobManager(tmp_path, fake_steps())
    s = m2.get(j.id)
    assert s.stage == "error" and s.failed_phase == "prepare"
    m2.stop()


def test_cleanup_old(tmp_path):
    m = JobManager(tmp_path, fake_steps())
    old = m.create("u1", RenderOptions())
    new = m.create("u2", RenderOptions())
    wait_stage(m, new.id, "ready")
    t = time.time() - 8 * 86400
    os.utime(tmp_path / old.id, (t, t))
    assert m.cleanup_old(7) == 1
    assert not (tmp_path / old.id).exists() and (tmp_path / new.id).exists()
    with pytest.raises(KeyError):
        m.get(old.id)
    m.stop()


def test_get_unknown(manager):
    with pytest.raises(KeyError):
        manager.get("nope")
