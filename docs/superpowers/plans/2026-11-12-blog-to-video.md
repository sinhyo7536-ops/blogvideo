# 블로그 → 영상 변환기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네이버 블로그 링크를 받아 사진·자막·AI 성우·배경음악 영상(MP4)을 만드는, 윈도우 설치 파일로 배포되는 로컬 웹 앱.

**Architecture:** FastAPI 단일 프로세스. 추출(scraper) → 대본(Claude) → 음성(edge-tts) → 렌더(FFmpeg, 자막은 Pillow로 PNG 생성) 파이프라인을 작업(job) 단위 폴더에서 백그라운드 스레드 1개가 순차 실행. 빌드 도구 없는 모바일 우선 HTML/JS 프론트엔드. PyInstaller + Inno Setup 설치 파일을 GitHub Actions(windows-latest)에서 빌드.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, beautifulsoup4, anthropic(SDK 2.x), edge-tts, Pillow, qrcode, pydantic 2, pytest; FFmpeg/ffprobe 바이너리; PyInstaller; Inno Setup 6.

**Spec:** `docs/superpowers/specs/2026-11-12-blog-to-video-design.md`

## Global Constraints

- 모든 사용자 문구는 쉬운 한국어. 오류 메시지는 스펙 §6 표의 문구를 그대로 쓴다.
- 해상도: 16:9 → 1920×1080, 9:16 → 1080×1920. 30fps, H.264(yuv420p) + AAC, `-movflags +faststart`.
- 성우: 여 `ko-KR-SunHiNeural`, 남 `ko-KR-InJoonNeural`. 장면 길이 = 음성 길이 + 0.4초.
- 장면 6~12개, 장면 자막 40자 이내 목표, 유튜브 제목 60자 이내.
- 배경음악 기본 볼륨 0.15, 끝 2초 페이드아웃. 장면 전환 페이드 0.3초.
- 서버 `0.0.0.0:8765`. 설정 API(`/api/settings*`)는 클라이언트 IP가 loopback일 때만 허용.
- 설정: `%APPDATA%\BlogVideo\config.json` (비윈도우: `~/.config/BlogVideo/config.json`). 작업: `%LOCALAPPDATA%\BlogVideo\jobs\{job_id}\` (비윈도우: `~/.local/share/BlogVideo/jobs`). 7일 지난 작업 폴더는 시작 시 삭제.
- Claude 모델 `claude-opus-5-5`, `output_config={"effort": "medium"}`, 구조화 출력(`client.messages.parse(output_format=PydanticModel)`). 거절(`stop_reason == "refusal"`)은 대본 실패로 처리.
- 외부 명령은 항상 `subprocess.run([...])` 리스트 인자(셸 문자열 금지) — 한글/공백 경로 대응.
- FFmpeg/ffprobe/폰트/음악 경로는 `app/paths.py`의 `resource_path()`로만 찾는다(PyInstaller `sys._MEIPASS` 대응).
- 동시에 실행되는 렌더/대본 작업은 1개(전역 작업 큐). 대기 중 작업은 "대기 중" 상태.

## Review Focus

1. **`naver.me` 단축 링크 / `blog.naver.com/PostView.naver?...` / 모바일 링크 / 쿼리스트링 붙은 링크** → 모두 같은 글로 해석되어야 함. (Task 2: `test_parse_url_variants`, `test_resolve_short_link`)
2. **사진이 0장인 글** → 실패하지 말고 단색 배경 + 자막으로 영상 생성. (Task 5: `test_render_without_images`)
3. **자막에 따옴표·콜론·%·이모지·아주 긴 문장** → 깨지거나 FFmpeg 오류 없이 줄바꿈되어 화면 안에 들어가야 함. (Task 5: `test_caption_wraps_long_text`, `test_caption_special_chars`)
4. **휴대폰에서 받은 파일 이름이 한글** → 다운로드 시 깨지지 않아야 함(RFC 5987 `filename*`). (Task 6: `test_download_korean_filename`)
5. **PC와 휴대폰에서 동시에 "영상 만들기"** → 둘 다 순서대로 처리되고 서로의 결과를 덮어쓰지 않아야 함. (Task 6: `test_two_jobs_queue_sequentially`)

---

## File Structure

```
pyproject.toml                 의존성, pytest 설정
app/__init__.py
app/models.py                  BlogPost, ImageRef, Scene, VideoScript, RenderOptions, JobState
app/paths.py                   resource_path(), config_dir(), jobs_dir(), ffmpeg_bin(), ffprobe_bin()
app/config.py                  load_config(), save_config()
app/errors.py                  UserError(message) — 사용자에게 그대로 보여 줄 오류
app/scraper/__init__.py
app/scraper/naver.py           parse_naver_url(), resolve_url(), extract_post(), fetch_post(), download_images()
app/script.py                  generate_script(), normalize_script()
app/tts.py                     synthesize_scene(), synthesize_all(), probe_duration()
app/captions.py                render_caption_png()
app/render.py                  render_video()
app/jobs.py                    JobManager (큐, 상태, 폴더)
app/netinfo.py                 lan_ip(), qr_svg()
app/main.py                    FastAPI 앱 + 라우트
app/web/index.html, app.css, app.js
assets/fonts/                  NotoSansKR-Bold.ttf + OFL.txt
assets/music/                  *.mp3 + LICENSES.md
packaging/launcher.py          진입점: 서버 시작 + 브라우저 열기
packaging/blogvideo.spec       PyInstaller spec
packaging/installer.iss        Inno Setup
.github/workflows/build-windows.yml
tests/...                      각 모듈 테스트, tests/fixtures/
docs/사용설명서.md, docs/배포안내.md
```

---

### Task 1: 프로젝트 골격, 모델, 경로, 설정

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/models.py`, `app/paths.py`, `app/config.py`, `app/errors.py`, `.gitignore`
- Test: `tests/test_config.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `models.ImageRef(url: str, local_path: str | None = None)`
  - `models.BlogPost(title: str, paragraphs: list[str], images: list[ImageRef], url: str)`
  - `models.Scene(text: str, image_index: int | None, duration: float | None = None, audio_path: str | None = None)` — `image_index=None`이면 단색 배경
  - `models.VideoScript(scenes: list[Scene], yt_title: str, yt_description: str, yt_tags: list[str])`
  - `models.RenderOptions(aspect: Literal["16:9","9:16"] = "16:9", voice: Literal["female","male"] = "female", bgm: str | None = None, bgm_volume: float = 0.15)`
  - `models.JobState(id: str, stage: Literal["queued","scraping","scripting","ready","tts","rendering","done","error"], progress: float, message: str, post: BlogPost | None, script: VideoScript | None, options: RenderOptions, video_path: str | None, created_at: float)`
  - 모두 pydantic `BaseModel`.
  - `paths.resource_path(rel: str) -> Path`, `paths.config_dir() -> Path`, `paths.jobs_dir() -> Path`, `paths.music_dirs() -> list[Path]` (내장 `assets/music` + 사용자 `config_dir()/music`), `paths.ffmpeg_bin() -> str`, `paths.ffprobe_bin() -> str` (번들 경로에 있으면 그것, 없으면 `"ffmpeg"`/`"ffprobe"`)
  - `config.load_config() -> dict`, `config.save_config(data: dict) -> None`; 키: `anthropic_api_key: str`
  - `errors.UserError(Exception)` — `.message: str`
  - 모든 경로 함수는 환경변수 `BLOGVIDEO_HOME`이 있으면 그 아래(`config/`, `jobs/`)를 쓴다(테스트 격리용).

- [ ] **Step 1: `pyproject.toml` 작성** — 의존성: `fastapi`, `uvicorn[standard]`, `httpx`, `beautifulsoup4`, `lxml`, `anthropic>=2.8`, `edge-tts>=7.3`, `pillow`, `qrcode`, `pydantic>=2`, `python-multipart`; dev: `pytest`, `pytest-asyncio`. `[tool.pytest.ini_options] testpaths=["tests"]`. `pip install -e .[dev]` 성공 확인.
- [ ] **Step 2: 실패 테스트 작성**

```python
def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path))
    assert load_config() == {}
    save_config({"anthropic_api_key": "sk-test"})
    assert load_config()["anthropic_api_key"] == "sk-test"

def test_render_options_defaults():
    o = RenderOptions()
    assert (o.aspect, o.voice, o.bgm, o.bgm_volume) == ("16:9", "female", None, 0.15)

def test_scene_without_image_allowed():
    assert Scene(text="안녕", image_index=None).image_index is None
```

- [ ] **Step 3: `pytest -q` → FAIL (ImportError) 확인**
- [ ] **Step 4: 구현** (`config.json`은 UTF-8, `ensure_ascii=False`)
- [ ] **Step 5: `pytest -q` → PASS**
- [ ] **Step 6: Commit** `feat: project skeleton, models, paths, config`

---

### Task 2: 네이버 블로그 추출기

**Files:**
- Create: `app/scraper/__init__.py`, `app/scraper/naver.py`
- Test: `tests/test_naver.py`, `tests/fixtures/naver_post.html`

**Interfaces:**
- Consumes: `BlogPost`, `ImageRef`, `UserError`
- Produces:
  - `parse_naver_url(url: str) -> tuple[str, str]` — `(blog_id, log_no)`; 해석 불가 시 `UserError("네이버 블로그 글 주소를 넣어 주세요.")`
  - `resolve_url(url: str, client: httpx.Client) -> str` — `naver.me` 단축 링크면 리다이렉트를 따라가 최종 URL 반환, 아니면 그대로
  - `extract_post(html: str, url: str) -> BlogPost` — 순수 함수(네트워크 없음)
  - `fetch_post(url: str) -> BlogPost` — resolve → `https://m.blog.naver.com/{blog_id}/{log_no}` GET → extract
  - `download_images(post: BlogPost, dest: Path) -> BlogPost` — 각 이미지를 `dest/img_{i:02d}.jpg`로 저장, `local_path` 채움, 실패한 이미지는 목록에서 제외

- [ ] **Step 1: 픽스처 준비** — 실제 공개 네이버 글 1편의 모바일 페이지 HTML을 받아 `tests/fixtures/naver_post.html`로 저장(본문 `.se-main-container`에 텍스트 문단 3개 이상, 이미지 2장 이상 포함된 글). 테스트의 기대값은 그 파일 내용 기준으로 적는다.
- [ ] **Step 2: 실패 테스트 작성**

```python
@pytest.mark.parametrize("url", [
    "https://blog.naver.com/abc123/223456789012",
    "https://m.blog.naver.com/abc123/223456789012",
    "https://blog.naver.com/abc123/223456789012?trackingCode=rss",
    "https://blog.naver.com/PostView.naver?blogId=abc123&logNo=223456789012",
    "https://m.blog.naver.com/PostView.naver?blogId=abc123&logNo=223456789012&navType=by",
])
def test_parse_url_variants(url):
    assert parse_naver_url(url) == ("abc123", "223456789012")

@pytest.mark.parametrize("url", ["https://tistory.com/x/1", "그냥 글자", "https://blog.naver.com/abc123"])
def test_parse_url_rejects(url):
    with pytest.raises(UserError):
        parse_naver_url(url)

def test_resolve_short_link():
    transport = httpx.MockTransport(lambda r: httpx.Response(
        302, headers={"location": "https://blog.naver.com/abc123/223456789012"}) if r.url.host == "naver.me"
        else httpx.Response(200))
    with httpx.Client(transport=transport, follow_redirects=True) as c:
        assert parse_naver_url(resolve_url("https://naver.me/xYz", c)) == ("abc123", "223456789012")

def test_extract_post_fixture():
    post = extract_post(Path("tests/fixtures/naver_post.html").read_text("utf-8"), "u")
    assert post.title          # 픽스처의 실제 제목과 일치
    assert len(post.paragraphs) >= 3 and all(p.strip() for p in post.paragraphs)
    assert len(post.images) >= 2 and all(i.url.startswith("http") for i in post.images)

def test_extract_post_without_container_raises():
    with pytest.raises(UserError, match="글을 읽을 수 없어요"):
        extract_post("<html><body>비공개</body></html>", "u")
```

- [ ] **Step 3: 실행 → FAIL 확인**
- [ ] **Step 4: 구현** — 제목: `.se-title-text` 또는 `og:title`. 문단: `.se-main-container`의 `.se-text-paragraph`를 순서대로, 빈 문단 제외, 연속 공백 정리. 이미지: `.se-image-resource`/`img`의 `data-lazy-src` 우선, 없으면 `src`; 쿼리스트링 `type=`를 `w966`으로 바꿔 고해상도화; 스티커·지도 아이콘(`storep-phinf`, `static`) 제외. 다운로드 시 `Referer: https://m.blog.naver.com/` 헤더, 일반 브라우저 User-Agent. 네트워크 오류는 `UserError("인터넷 연결을 확인해 주세요.")`.
- [ ] **Step 5: 실행 → PASS**
- [ ] **Step 6: 실제 네트워크 확인(수동)** — `python -c "from app.scraper.naver import fetch_post; p=fetch_post('<픽스처 글 URL>'); print(p.title, len(p.paragraphs), len(p.images))"` 출력이 비어 있지 않음.
- [ ] **Step 7: Commit** `feat: naver blog scraper`

---

### Task 3: Claude 대본 생성

**Files:**
- Create: `app/script.py`
- Test: `tests/test_script.py`

**Interfaces:**
- Consumes: `BlogPost`, `VideoScript`, `Scene`, `UserError`
- Produces:
  - `generate_script(post: BlogPost, api_key: str, client: anthropic.Anthropic | None = None) -> VideoScript`
  - `normalize_script(raw: VideoScript, image_count: int) -> VideoScript`

- [ ] **Step 1: 실패 테스트 작성** (Claude 클라이언트는 가짜 객체로 대체: `.messages.parse(...)`가 `SimpleNamespace(stop_reason="end_turn", parsed_output=...)` 반환)

```python
def test_normalize_clamps_image_index():
    s = normalize_script(VideoScript(scenes=[Scene(text="a", image_index=9), Scene(text="b", image_index=-1)],
                                     yt_title="t", yt_description="d", yt_tags=[]), image_count=3)
    assert [x.image_index for x in s.scenes] == [0, 1]   # 범위 밖은 장면 순서대로 순환 배정

def test_normalize_no_images_sets_none():
    s = normalize_script(VideoScript(scenes=[Scene(text="a", image_index=0)], yt_title="t",
                                     yt_description="d", yt_tags=[]), image_count=0)
    assert s.scenes[0].image_index is None

def test_normalize_trims_title_and_drops_empty_scenes():
    s = normalize_script(VideoScript(scenes=[Scene(text="  ", image_index=0), Scene(text="b", image_index=0)],
                                     yt_title="가" * 80, yt_description="d", yt_tags=["#태그", "태그"]), 1)
    assert len(s.scenes) == 1 and len(s.yt_title) <= 60 and s.yt_tags == ["태그"]

def test_generate_retries_once_on_invalid_then_succeeds(fake_client_invalid_then_valid, sample_post):
    s = generate_script(sample_post, "k", client=fake_client_invalid_then_valid)
    assert 1 <= len(s.scenes) and fake_client_invalid_then_valid.calls == 2

def test_generate_fails_after_two_invalid(fake_client_always_invalid, sample_post):
    with pytest.raises(UserError, match="대본을 만들지 못했어요"):
        generate_script(sample_post, "k", client=fake_client_always_invalid)

def test_generate_auth_error_message(sample_post):  # 가짜 클라이언트가 anthropic.AuthenticationError 발생
    with pytest.raises(UserError, match="API 키를 확인"):
        generate_script(sample_post, "k", client=fake_client_raising_auth())
```
"invalid" = `parsed_output`가 `None`이거나 장면 0개, 또는 `stop_reason == "refusal"`.

- [ ] **Step 2: 실행 → FAIL**
- [ ] **Step 3: 구현** — `client.messages.parse(model="claude-opus-5-5", max_tokens=16000, output_config={"effort": "medium"}, system=..., messages=[...], output_format=VideoScript)`. 프롬프트(시스템): 한국어 유튜브 내레이션 작가 역할; 장면 6~12개; 장면당 1~2문장·40자 내외 구어체; `image_index`는 0부터 `image_count-1`, 글 흐름상 가까운 사진; 첫 장면은 시선을 끄는 도입, 마지막 장면은 마무리 인사/구독 유도; 원문에 없는 사실 추가 금지; 제목 60자 이내, 설명 3~5줄 + 원문 링크, 태그 5~10개. 사용자 메시지에 제목, 번호 붙인 문단, 이미지 개수, 원문 URL. 오류 매핑: `AuthenticationError`/`PermissionDeniedError` → "설정에서 API 키를 확인해 주세요.", `APIConnectionError` → "인터넷 연결을 확인해 주세요.", 그 외 `APIStatusError`/형식 오류 2회 → "대본을 만들지 못했어요. 다시 시도해 주세요." 결과는 `normalize_script`를 거쳐 반환. 설치된 SDK 2.x에서 `messages.parse`의 `output_format` 인자명을 `python -c "import anthropic, inspect; print(inspect.signature(anthropic.Anthropic().messages.parse))"`로 확인 후 사용.
- [ ] **Step 4: 실행 → PASS**
- [ ] **Step 5: (선택, 키가 있을 때) 실제 호출 1회** — 픽스처 글로 `generate_script` 실행, 장면 6~12개 확인.
- [ ] **Step 6: Commit** `feat: claude script generation`

---

### Task 4: Edge TTS 음성

**Files:**
- Create: `app/tts.py`
- Test: `tests/test_tts.py`

**Interfaces:**
- Consumes: `Scene`, `VideoScript`, `paths.ffprobe_bin`, `UserError`
- Produces:
  - `VOICES = {"female": "ko-KR-SunHiNeural", "male": "ko-KR-InJoonNeural"}`
  - `probe_duration(path: Path) -> float`
  - `async synthesize_scene(text: str, voice: str, out: Path) -> None` (`edge_tts.Communicate(text, voice).save(out)`)
  - `synthesize_all(script: VideoScript, voice_key: str, dest: Path, on_progress: Callable[[float], None] | None = None, synth=synthesize_scene) -> VideoScript` — 장면별 `dest/voice_{i:02d}.mp3`, `audio_path`·`duration`(= 길이 + 0.4) 채운 새 스크립트 반환. 장면별 실패 시 2회까지 재시도, 그래도 실패하면 `UserError("음성을 만들지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.")`

- [ ] **Step 1: 실패 테스트 작성** — 가짜 `synth`가 `ffmpeg -f lavfi -i anullsrc=r=24000:cl=mono -t 1.0 out.mp3`로 1초짜리 무음 파일 생성.

```python
def test_synthesize_all_sets_durations(tmp_path, short_script):
    s = synthesize_all(short_script, "female", tmp_path, synth=fake_synth_1s)
    assert all(abs(x.duration - 1.4) < 0.1 for x in s.scenes)
    assert all(Path(x.audio_path).exists() for x in s.scenes)

def test_synthesize_retries_then_fails(tmp_path, short_script):
    with pytest.raises(UserError, match="음성을 만들지 못했어요"):
        synthesize_all(short_script, "female", tmp_path, synth=always_failing_synth)

def test_progress_reported(tmp_path, short_script):
    seen = []
    synthesize_all(short_script, "male", tmp_path, on_progress=seen.append, synth=fake_synth_1s)
    assert seen[-1] == 1.0
```

- [ ] **Step 2: 실행 → FAIL**
- [ ] **Step 3: 구현** (`asyncio.run`으로 장면 순차 실행; `probe_duration`은 `ffprobe -v error -show_entries format=duration -of csv=p=0`)
- [ ] **Step 4: 실행 → PASS**
- [ ] **Step 5: 실제 Edge TTS 1회(수동)** — `python -c "import asyncio; from app.tts import synthesize_scene; asyncio.run(synthesize_scene('안녕하세요, 테스트입니다.', 'ko-KR-SunHiNeural', __import__('pathlib').Path('/tmp/t.mp3')))"` 후 파일 크기 > 0.
- [ ] **Step 6: Commit** `feat: edge tts narration`

---

### Task 5: 자막 이미지 + FFmpeg 렌더러, 폰트·음악 에셋

**Files:**
- Create: `app/captions.py`, `app/render.py`, `assets/fonts/NotoSansKR-Bold.ttf`, `assets/fonts/OFL.txt`, `assets/music/*.mp3`, `assets/music/LICENSES.md`
- Test: `tests/test_captions.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `VideoScript`(duration·audio_path 채워진 것), `BlogPost`(images.local_path), `RenderOptions`, `paths.*`
- Produces:
  - `render_caption_png(text: str, width: int, height: int, out: Path) -> Path` — 투명 배경 RGBA PNG(영상 크기와 동일). 하단 반투명 검정 띠(알파 160) + 흰 글씨 + 검정 외곽선 4px. 글자 크기: 가로 영상 64px, 세로 영상 72px. 띠 폭의 88%를 넘으면 단어 단위 줄바꿈(단어 하나가 넘으면 글자 단위), 최대 3줄, 넘치면 글자 크기를 줄임(최소 40px). 세로 영상은 띠를 아래에서 22% 위치에 둠(유튜브 쇼츠 UI 회피).
  - `render_video(post: BlogPost, script: VideoScript, options: RenderOptions, workdir: Path, on_progress: Callable[[float], None] | None = None) -> Path` — `workdir/output.mp4` 반환. 실패 시 `UserError("영상을 만들지 못했어요.")`, FFmpeg stderr는 `workdir/render.log`에 저장.

- [ ] **Step 1: 에셋 추가** — Noto Sans KR Bold(OFL)와 OFL.txt. 음악은 CC0 또는 퍼블릭 도메인임을 출처 페이지에서 확인할 수 있는 곡 3~5개만 넣고, `LICENSES.md`에 곡명·작곡자·출처 URL·라이선스를 적는다. 확인 가능한 곡을 받을 수 없으면 음악 폴더는 비워 두고 LICENSES.md에 사유를 적은 뒤 사용자에게 보고한다(사용자 MP3 추가 기능은 그대로 동작).
- [ ] **Step 2: 실패 테스트 작성**

```python
def test_caption_wraps_long_text(tmp_path):
    p = render_caption_png("아주 " * 60, 1080, 1920, tmp_path / "c.png")
    im = Image.open(p); assert im.size == (1080, 1920) and im.mode == "RGBA"
    bbox = im.getbbox(); assert bbox[0] >= 0 and bbox[2] <= 1080   # 화면 밖으로 안 나감

def test_caption_special_chars(tmp_path):
    render_caption_png('그는 "50% 할인: 오늘만!" 이라고 했다 😀', 1920, 1080, tmp_path / "c.png")

@pytest.mark.parametrize("aspect,size", [("16:9", (1920, 1080)), ("9:16", (1080, 1920))])
def test_render_video_dimensions_and_length(tmp_path, aspect, size, two_scene_inputs):
    post, script = two_scene_inputs(tmp_path)        # 컬러 테스트 이미지 2장, 1초 무음 음성 2개
    out = render_video(post, script, RenderOptions(aspect=aspect), tmp_path)
    info = ffprobe_json(out)
    assert (info.width, info.height) == size
    assert abs(info.duration - sum(s.duration for s in script.scenes)) < 0.6
    assert info.has_audio

def test_render_without_images(tmp_path, two_scene_inputs):
    post, script = two_scene_inputs(tmp_path, with_images=False)
    assert render_video(post, script, RenderOptions(), tmp_path).exists()

def test_render_with_bgm(tmp_path, two_scene_inputs, silent_mp3):
    post, script = two_scene_inputs(tmp_path)
    assert render_video(post, script, RenderOptions(bgm=str(silent_mp3)), tmp_path).exists()

def test_render_progress_reaches_one(tmp_path, two_scene_inputs):
    seen = []; post, script = two_scene_inputs(tmp_path)
    render_video(post, script, RenderOptions(), tmp_path, on_progress=seen.append)
    assert seen and seen[-1] == 1.0
```

- [ ] **Step 3: 실행 → FAIL**
- [ ] **Step 4: `captions.py` 구현 → caption 테스트 PASS** (이모지는 폰트에 없으면 제거 후 렌더)
- [ ] **Step 5: `render.py` 구현** — 장면별 클립을 먼저 만들고(`scene_{i:02d}.mp4`), 마지막에 합친다.
  - 장면 클립: 입력 = 이미지(`-loop 1`) 또는 `color=c=0x1e1e28` 단색, 자막 PNG, 음성. 필터: `scale=W*1.2:H*1.2:force_original_aspect_ratio=increase,crop=W*1.2:H*1.2` → `zoompan=z='min(zoom+0.0008,1.12)':d=frames:s=WxH:fps=30` → 자막 `overlay` → `fade=in:0:9,fade=out:st=dur-0.3:d=0.3`. 오디오: 음성 + `apad`로 장면 길이까지. `-t duration`.
  - 합치기: concat demuxer(`-f concat -safe 0`, 목록 파일 UTF-8) → BGM이 있으면 `-stream_loop -1`로 반복, `volume=bgm_volume`, `afade=out:st=total-2:d=2`, `amix=inputs=2:duration=first:normalize=0`. 출력 `-c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart`.
  - 진행률: 장면 클립 단계 0~0.9(장면 수 비례), 합치기 0.9~1.0. 끝나면 `on_progress(1.0)`.
- [ ] **Step 6: 실행 → 전체 render/caption 테스트 PASS**
- [ ] **Step 7: 눈으로 확인** — 테스트 결과 영상에서 프레임 하나를 `ffmpeg -ss 1 -i output.mp4 -frames:v 1 frame.png`로 뽑아 자막·확대 효과 확인(가로/세로 각각).
- [ ] **Step 8: Commit** `feat: caption overlay and ffmpeg renderer with bundled assets`

---

### Task 6: 작업 관리자 + FastAPI 라우트

**Files:**
- Create: `app/jobs.py`, `app/netinfo.py`, `app/main.py`
- Test: `tests/test_jobs.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `fetch_post`, `download_images`, `generate_script`, `synthesize_all`, `render_video`, `load_config`, `save_config`, `JobState`, `UserError`
- Produces:
  - `JobManager(root: Path, steps: Steps)` — `Steps`는 위 5개 함수를 묶은 dataclass(테스트에서 가짜로 교체). 메서드:
    - `create(url: str, options: RenderOptions) -> JobState` — 큐에 "추출+대본" 작업 등록, stage `queued`
    - `get(job_id: str) -> JobState` (없으면 `KeyError`)
    - `update_script(job_id: str, script: VideoScript, options: RenderOptions) -> JobState` — stage가 `ready`/`done`/`error`일 때만
    - `start_render(job_id: str) -> JobState` — 큐에 "음성+렌더" 등록
    - `retry(job_id: str) -> JobState` — 실패한 단계부터 다시 큐에 등록
    - `cleanup_old(days: int = 7) -> int`
    - 작업 상태는 `root/{id}/state.json`에 저장(재시작 후에도 조회 가능). 워커 스레드 1개가 큐를 순차 처리.
  - `netinfo.lan_ip() -> str` (UDP 소켓 트릭, 실패 시 `127.0.0.1`), `netinfo.qr_svg(text: str) -> str`
  - 라우트 (`app = create_app(manager: JobManager | None = None)`):
    - `GET /` → `web/index.html`; `/static/*` → `app/web`
    - `GET /api/info` → `{"lan_url": "http://{ip}:8765", "qr_svg": str, "has_api_key": bool, "is_local": bool}`
    - `POST /api/jobs` `{url, options}` → `JobState`; API 키 없으면 400 `{"message": "설정에서 API 키를 입력해 주세요."}`
    - `GET /api/jobs/{id}` → `JobState`
    - `PUT /api/jobs/{id}/script` `{script, options}` → `JobState`
    - `POST /api/jobs/{id}/render`, `POST /api/jobs/{id}/retry` → `JobState`
    - `GET /api/jobs/{id}/image/{i}` → 이미지 파일(장면 편집 미리보기용)
    - `GET /api/jobs/{id}/video` → MP4 (`Range` 지원 — `FileResponse`), `?download=1`이면 `Content-Disposition: attachment; filename*=UTF-8''{quote(yt_title)}.mp4`
    - `GET /api/music` → `[{"name": str, "builtin": bool}]`
    - `POST /api/music` (multipart mp3, **localhost 전용**) → 사용자 음악 폴더에 저장
    - `GET /api/settings`, `PUT /api/settings` `{anthropic_api_key}` — **localhost 전용**, 아니면 403 `{"message": "설정은 PC에서만 바꿀 수 있어요."}`; GET은 키를 `sk-ant-…끝4자리`로 가려서 반환
    - 모든 `UserError`는 400 `{"message": e.message}`, 기타 예외는 500 `{"message": "알 수 없는 오류가 났어요. 다시 시도해 주세요."}` + 로그

- [ ] **Step 1: 실패 테스트 작성** (가짜 Steps: 즉시 성공하는 함수들, render는 빈 파일 생성)

```python
def test_job_flow_to_done(manager):
    j = manager.create("https://blog.naver.com/a/1", RenderOptions())
    wait_stage(manager, j.id, "ready")
    manager.start_render(j.id); wait_stage(manager, j.id, "done")
    assert Path(manager.get(j.id).video_path).exists()

def test_failed_step_sets_error_and_retry(manager_failing_once):
    j = manager_failing_once.create("u", RenderOptions())
    wait_stage(manager_failing_once, j.id, "error")
    assert manager_failing_once.get(j.id).message == "글을 읽을 수 없어요. 전체공개 글인지 확인해 주세요."
    manager_failing_once.retry(j.id); wait_stage(manager_failing_once, j.id, "ready")

def test_two_jobs_queue_sequentially(manager_slow):
    a = manager_slow.create("u1", RenderOptions()); b = manager_slow.create("u2", RenderOptions())
    assert manager_slow.get(b.id).stage == "queued"
    wait_stage(manager_slow, b.id, "ready")
    assert manager_slow.get(a.id).post.url != manager_slow.get(b.id).post.url

def test_cleanup_old(manager, tmp_path): ...  # mtime 8일 전 폴더 삭제, 1일 전 폴더 유지

def test_settings_forbidden_from_lan(client_from_ip("192.168.0.5")):
    assert client.put("/api/settings", json={"anthropic_api_key": "x"}).status_code == 403

def test_settings_allowed_from_localhost(client_from_ip("127.0.0.1")):
    assert client.put("/api/settings", json={"anthropic_api_key": "sk-ant-abcd1234"}).status_code == 200
    assert client.get("/api/settings").json()["anthropic_api_key"].endswith("1234")

def test_create_job_without_key_400(client_no_key):
    r = client_no_key.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "options": {}})
    assert r.status_code == 400 and "API 키" in r.json()["message"]

def test_download_korean_filename(client_with_done_job):
    r = client.get(f"/api/jobs/{job_id}/video?download=1")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]

def test_video_supports_range(client_with_done_job):
    assert client.get(f"/api/jobs/{job_id}/video", headers={"Range": "bytes=0-9"}).status_code == 206
```
`client_from_ip`는 `TestClient(app, client=(ip, 50000))`로 클라이언트 주소 지정.

- [ ] **Step 2: 실행 → FAIL**
- [ ] **Step 3: `jobs.py`, `netinfo.py`, `main.py` 구현** — 추출 단계: `fetch_post` → `download_images(post, job_dir)`; 대본 단계: `generate_script(post, load_config()["anthropic_api_key"])`; 렌더 단계: `synthesize_all` → `render_video`. 진행률: scraping 0~0.3, scripting 0.3~1.0(대본 단계 자체 진행률), tts 0~0.2, rendering 0.2~1.0.
- [ ] **Step 4: 실행 → PASS**
- [ ] **Step 5: Commit** `feat: job manager and http api`

---

### Task 7: 모바일 우선 웹 화면

**Files:**
- Create: `app/web/index.html`, `app/web/app.css`, `app/web/app.js`
- Test: `tests/test_web_smoke.py` (Playwright, 서버를 가짜 Steps로 띄움)

**Interfaces:**
- Consumes: Task 6 라우트 전부
- Produces: 한 페이지 앱. 화면 상태: `home` → `edit` → `progress` → `result`, 그리고 `settings`(PC에서만 메뉴 노출: `/api/info`의 `is_local`).

화면 구성(문구 확정):
- **home**: 제목 "블로그 글로 영상 만들기"; 입력 "네이버 블로그 글 주소"; 가로/세로 토글("가로 영상(유튜브)" / "세로 영상(쇼츠)"); 성우("여자 목소리"/"남자 목소리"); 배경음악 선택("배경음악 없음" + 목록); 버튼 "대본 만들기". PC 화면이면 오른쪽(모바일은 하단 접힘)에 "휴대폰으로 접속하기" QR + 주소.
- **edit**: 장면 카드 목록 — 사진 썸네일(누르면 이 글의 사진 중 선택하는 시트, "사진 없음" 포함), 자막 textarea(글자 수 표시, 40자 넘으면 주황색), 위/아래 이동, 삭제. 하단: 유튜브 제목/설명/태그 입력. 고정 하단 버튼 "영상 만들기".
- **progress**: 단계 이름("글 읽는 중", "대본 쓰는 중", "목소리 만드는 중", "영상 만드는 중"), 진행 막대, 퍼센트. 1초마다 `GET /api/jobs/{id}` 폴링.
- **result**: `<video controls playsinline>` 미리보기, "영상 저장" 버튼(`?download=1`), 제목/설명/태그 각각 "복사" 버튼(`navigator.clipboard`, 실패 시 텍스트 선택 폴백), "대본 고쳐서 다시 만들기", "새 영상 만들기". 안내 문구: "저장한 영상을 유튜브 앱에서 올리고, 복사한 제목·설명·태그를 붙여 넣으세요."
- **error**: 서버 `message` 그대로 + "다시 시도" 버튼(`/retry`).
- 현재 작업 id를 `localStorage`에 저장해 새로고침해도 이어서 보기.
- CSS: 모바일 우선, 기본 글자 17px, 버튼 최소 높이 48px, 최대 폭 720px 가운데 정렬, 넓은 화면에서 장면 카드 2열.

- [ ] **Step 1: 실패 스모크 테스트 작성** (Playwright, 뷰포트 390×844와 1280×800 두 번)

```python
def test_full_flow_mobile_and_desktop(live_server, viewport):
    page.goto(live_server); page.fill("#url", "https://blog.naver.com/a/1"); page.click("text=대본 만들기")
    page.wait_for_selector(".scene-card"); page.fill(".scene-card textarea >> nth=0", "고친 자막")
    page.click("text=영상 만들기"); page.wait_for_selector("video")
    assert page.is_visible("text=영상 저장")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")  # 가로 스크롤 없음
```

- [ ] **Step 2: 실행 → FAIL**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 실행 → PASS**
- [ ] **Step 5: 스크린샷 확인** — 두 뷰포트에서 각 화면 스크린샷을 찍어 눈으로 확인(겹침, 잘림, 버튼 크기).
- [ ] **Step 6: Commit** `feat: mobile-first web ui`

---

### Task 8: 실행기(launcher)와 실제 통합 확인

**Files:**
- Create: `packaging/launcher.py`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `create_app`, `JobManager`, `paths.jobs_dir`
- Produces: `main() -> None` — 이미 8765 포트에서 이 앱이 떠 있으면(`GET /api/info` 성공) 브라우저만 열고 종료; 아니면 `cleanup_old()` → uvicorn을 `0.0.0.0:8765`로 실행(별도 스레드) → 서버 준비되면 `webbrowser.open("http://127.0.0.1:8765")`. 로그는 `config_dir()/logs/app.log`(회전 1MB×3). 포트가 다른 프로그램에 점유돼 있으면 윈도우 메시지 상자(`ctypes.windll.user32.MessageBoxW`, 비윈도우는 stderr)로 "프로그램을 시작하지 못했어요. 컴퓨터를 다시 켠 뒤 실행해 주세요." 표시.

- [ ] **Step 1: 실패 테스트** — `test_second_launch_only_opens_browser`(첫 서버를 띄운 뒤 `main()` 재호출 시 `webbrowser.open`만 호출되고 새 서버 미기동, `monkeypatch`로 확인).
- [ ] **Step 2: 실행 → FAIL → 구현 → PASS**
- [ ] **Step 3: 실제 통합 실행** — 실제 키(있을 때)와 픽스처 네이버 글 URL로 `python packaging/launcher.py` → 브라우저(Playwright) 로 전체 흐름 → 나온 MP4를 `ffprobe`로 확인(가로·세로 각 1회). 키가 없으면 대본 단계만 가짜로 바꿔서 실행하고 그 사실을 보고.
- [ ] **Step 4: Commit** `feat: desktop launcher`

---

### Task 9: 윈도우 패키징과 CI 빌드

**Files:**
- Create: `packaging/blogvideo.spec`, `packaging/installer.iss`, `packaging/icon.ico`, `.github/workflows/build-windows.yml`

**Interfaces:**
- Consumes: `packaging/launcher.py`, `app/web/*`, `assets/*`
- Produces: GitHub Actions 아티팩트 `BlogVideo-Setup.exe`(파일 이름 `블로그영상변환기_설치.exe`로 릴리스에 첨부)

- [ ] **Step 1: PyInstaller spec** — onedir, 콘솔 숨김(`console=False`), `datas`: `app/web`, `assets`, `ffmpeg/ffmpeg.exe`, `ffmpeg/ffprobe.exe`, FFmpeg 라이선스 파일; `hiddenimports`: `uvicorn` 하위 모듈, `edge_tts`. `paths.resource_path`가 `sys._MEIPASS` 기준으로 찾도록 맞춘다.
- [ ] **Step 2: Inno Setup** — `AppName=블로그 영상 변환기`, 방화벽 규칙 추가에 관리자 권한이 필요하므로 `PrivilegesRequired=admin`, 설치 경로 `{autopf}\BlogVideo`, 한국어 메시지(`Languages: Korean.isl`), 바탕화면·시작 메뉴 바로가기, 설치 후 실행 체크박스. `[Run]`: `netsh advfirewall firewall add rule name="BlogVideo" dir=in action=allow protocol=TCP localport=8765 profile=private`; `[UninstallRun]`: 같은 이름 규칙 삭제.
- [ ] **Step 3: 워크플로** — `on: workflow_dispatch, push tags v*`; `windows-latest`; Python 3.12 설치 → `pip install -e . pyinstaller` → `pytest -q`(Playwright 테스트 제외 마커 `-m "not e2e"`) → FFmpeg 윈도우 정적 빌드(gyan.dev release essentials zip, SHA256 검증) 내려받아 `ffmpeg/`에 풀기 → `pyinstaller packaging/blogvideo.spec` → 스모크: 빌드된 exe를 백그라운드 실행 후 `Invoke-WebRequest http://127.0.0.1:8765/api/info` 200 확인 후 종료 → `choco install innosetup` → `iscc packaging/installer.iss` → `actions/upload-artifact`; 태그 푸시면 릴리스에 첨부.
- [ ] **Step 4: 푸시 후 워크플로 실행(수동 dispatch) → 성공 확인**, 실패하면 로그로 고치기. 아티팩트 다운로드 링크 확보.
- [ ] **Step 5: Commit** `build: windows installer via github actions`

---

### Task 10: 안내 문서

**Files:**
- Create: `docs/사용설명서.md`, `docs/배포안내.md`, `README.md`

- [ ] **Step 1: `docs/배포안내.md`(사용자용)** — ① Anthropic 콘솔에서 API 키 발급 ② 월 사용 한도 설정 방법 ③ GitHub Actions/릴리스에서 설치 파일 받는 법 ④ 지인 PC에 설치 후 설정 화면에서 키 입력 ⑤ 업데이트 방법(새 설치 파일로 덮어쓰기, 설정 유지).
- [ ] **Step 2: `docs/사용설명서.md`(지인용)** — 설치(SmartScreen "추가 정보 → 실행" 안내 포함), 실행, 휴대폰 연결(QR, 같은 와이파이), 영상 만들기 4단계, 유튜브 앱으로 올리기, 자주 묻는 문제(휴대폰이 안 열려요 → 같은 와이파이/PC 켜짐/프로그램 실행 확인; "글을 읽을 수 없어요" → 전체공개 확인). 화면 캡처는 Task 7의 스크린샷을 `docs/images/`에 넣어 사용.
- [ ] **Step 3: `README.md`** — 개발자용 짧은 소개, 개발 실행(`pip install -e .[dev]`, `python packaging/launcher.py`), 테스트, 빌드.
- [ ] **Step 4: Commit** `docs: user and distributor guides`

---

## Self-Review 결과

- 스펙 커버리지: §3 흐름 → Task 6·7, §4.2 각 모듈 → Task 2~5, §4.4 휴대폰 접속·localhost 설정 → Task 6·9, §5 배포 → Task 9, §6 오류 문구 → Task 2·3·4·5·6, §7 테스트 → 각 Task, §9 산출물 → Task 9·10. 배경음악 추가(사용자 MP3) → Task 6 `POST /api/music` + Task 7 settings.
- 이름 일관성: `render_video`, `synthesize_all`, `generate_script`, `fetch_post`, `download_images`, `JobManager` — Task 6 Consumes와 일치.
- Review Focus 5개 모두 해당 Task 테스트에 반영.
