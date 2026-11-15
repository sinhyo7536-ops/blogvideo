import time
from pathlib import Path

from app.errors import UserError
from app.jobs import JobManager, Steps
from app.models import BlogPost, ImageRef, Scene, VideoScript


def fake_steps(delay=0.0, fail_fetch_times=0, key="sk-ant-test1234"):
    state = {"fails": fail_fetch_times}

    def fetch_post(url):
        time.sleep(delay)
        if state["fails"] > 0:
            state["fails"] -= 1
            raise UserError("글을 읽을 수 없어요. 전체공개 글인지 확인해 주세요.")
        return BlogPost(title="제목: 테스트/글", paragraphs=["p"], images=[ImageRef(url="i0")], url=url)

    def download_images(post, dest):
        dest.mkdir(parents=True, exist_ok=True)
        p = dest / "img_00.jpg"
        p.write_bytes(b"\xff\xd8fake")
        return post.model_copy(update={"images": [ImageRef(url="i0", local_path=str(p))]})

    def generate_script(post, api_key):
        assert api_key == key
        return VideoScript(scenes=[Scene(text="장면", image_index=0)], yt_title=post.title,
                           yt_description="설명", yt_tags=["태그"])

    def synthesize_all(script, voice, dest, on_progress=None, speed="normal"):
        if on_progress:
            on_progress(1.0)
        return script.model_copy(update={"scenes": [s.model_copy(update={"duration": 1.0, "audio_path": "a"})
                                                     for s in script.scenes]})

    def render_video(post, script, options, workdir, on_progress=None):
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "output.mp4"
        out.write_bytes(b"0123456789" * 100)
        if on_progress:
            on_progress(1.0)
        return out

    def voice_preview(voice, speed, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(f"mp3:{voice}:{speed}".encode())
        return out

    def fetch_music(url, dest):
        if "bad" in url:
            raise UserError("유튜브에서 음악을 가져오지 못했어요. 주소가 맞는지, 볼 수 있는 영상인지 확인해 주세요.")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "유튜브 곡.mp3").write_bytes(b"mp3 from " + url.encode())
        return "유튜브 곡.mp3"

    return Steps(fetch_post=fetch_post, download_images=download_images, generate_script=generate_script,
                 synthesize_all=synthesize_all, render_video=render_video, api_key=lambda: key,
                 voice_preview=voice_preview, fetch_music=fetch_music)


def wait_stage(manager: JobManager, job_id: str, stage: str, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        s = manager.get(job_id)
        if s.stage == stage:
            return s
        time.sleep(0.02)
    raise AssertionError(f"stage {manager.get(job_id).stage} != {stage}")
