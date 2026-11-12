# 블로그 영상 변환기 (blogvideo)

네이버 블로그 글 링크를 넣으면 블로그 사진 + 큰 자막 + AI 성우 + 배경음악으로 된 유튜브용 영상(MP4)을 만드는 로컬 웹 앱입니다.
윈도우 PC에서 실행되며, 보안을 위해 그 PC 안(127.0.0.1)에서만 열립니다. API 키는 윈도우 DPAPI로 암호화해 저장합니다.

- 사용자용: [docs/사용설명서.md](docs/사용설명서.md)
- 배포자용: [docs/배포안내.md](docs/배포안내.md)
- 설계: [docs/superpowers/specs/2026-11-12-blog-to-video-design.md](docs/superpowers/specs/2026-11-12-blog-to-video-design.md)

## 구조

| 모듈 | 하는 일 |
|---|---|
| `app/scraper/naver.py` | 네이버 블로그 글 제목·문단·사진 추출, 사진 다운로드 |
| `app/script.py` | Claude API(`claude-opus-5-5`, 구조화 출력)로 장면 대본 + 유튜브 제목/설명/태그 생성 |
| `app/autoscript.py` | AI 없이 블로그 문장을 장면으로 나누기 (무료) |
| `app/gemini.py` | Gemini 무료 API로 장면 대본 생성 (Claude와 같은 프롬프트·형식) |
| `app/visuals.py` | 영상 스타일 3종(깔끔/감성/브이로그), 제목 띠·장소 이름표, 시작·끝 화면, 썸네일 |
| `app/tts.py` | Edge TTS로 장면별 내레이션 (`ko-KR-SunHiNeural` / `ko-KR-InJoonNeural`) |
| `app/captions.py` | Pillow로 자막 PNG 생성 (줄바꿈, 외곽선) |
| `app/render.py` | FFmpeg로 장면 조각(사진 확대·이동, 제목 띠, 떠오르는 자막) → 전환 효과(xfade)로 잇기 → 목소리·배경음악 → 썸네일 |
| `app/jobs.py` | 작업 큐(한 번에 하나), 상태 저장, 재시도 |
| `app/main.py` | FastAPI 라우트, 이 PC·이 화면에서 온 요청만 허용 (Host/Origin 검사) |
| `app/config.py`, `app/secret.py` | 설정 저장, API 키 DPAPI 암호화 |
| `app/web/` | 모바일 우선 화면 (빌드 도구 없음) |
| `app/launcher.py` | 실행 진입점: 서버 시작 + 브라우저 열기 |
| `packaging/` | PyInstaller spec, Inno Setup 스크립트, 배경음악 생성기 |

## 개발

FFmpeg가 PATH에 있어야 합니다.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m app.launcher
```

테스트 (브라우저 테스트 제외):

```bash
.venv/bin/pytest -q -m "not e2e"
```

브라우저 테스트까지 (Playwright Chromium 필요):

```bash
.venv/bin/pytest -q
```

## 빌드

어느 브랜치든 푸시하면 `.github/workflows/build-windows.yml` 이 윈도우에서 테스트 → PyInstaller → Inno Setup 순으로 `BlogVideo-Setup.exe` 를 만듭니다.
`v*` 태그를 푸시하면 릴리스에도 첨부됩니다.

배경음악을 다시 만들려면:

```bash
.venv/bin/python packaging/make_music.py
```
