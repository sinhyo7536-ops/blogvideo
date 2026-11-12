# CLAUDE.md

네이버 블로그 글 → 유튜브용 MP4를 만드는 윈도우 데스크톱 앱(FastAPI 로컬 서버 + Edge 앱 창).
**전체 맥락과 남은 일은 `docs/인수인계.md`를 먼저 읽을 것.**

## 사용자 선호
- 질문·답은 한국어. 결정이 필요한 것은 바로 정하지 말고 먼저 물어볼 것(질문 개수 무관).
- 실사용자는 컴퓨터 초보(지인). 화면 문구는 쉬운 한국어.

## 명령 (윈도우)
- 설치: `.venv\Scripts\python -m pip install -e ".[dev]"`
- 실행: `.venv\Scripts\python -m app.launcher`
- 빠른 테스트: `.venv\Scripts\python -m pytest -q -m "not e2e"`
- 전체 테스트: `.venv\Scripts\python -m pytest -q` (Playwright Chromium 필요. 없으면 환경변수 `PW_CHROMIUM`에 Edge 경로 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` 를 넣어도 된다)
- 설치 파일: 브랜치에 푸시 → GitHub Actions `Build Windows installer` 산출물 `BlogVideo-Setup`

## 규칙
- 저장소는 **공개**다. API 키를 커밋·CI·산출물에 넣지 말 것(키 포함 설치 파일은 앱 설정 화면에서 로컬로만 생성).
- 새 기능·버그 수정은 실패하는 테스트를 먼저 쓰고(TDD) 고친다. 외부 API(Claude, Gemini, Edge TTS, 네이버)는 테스트에서 가짜로 대체(`tests/fakes.py`).
- `packaging/`에 `__init__.py` 금지(파이썬 `packaging` 라이브러리를 가림).
- `packaging/installer.iss`는 UTF-8 **BOM**으로 저장.
- 확대 효과에 FFmpeg `zoompan` 사용 금지(떨림). `render.py`의 2배 크기 scale→crop→축소 방식을 유지.
- 외부 명령은 `subprocess.run([...])` 리스트 인자 + `procutil.quiet_kwargs()`.
- 서버는 `127.0.0.1` 전용. 새 API도 `main.py`의 이 PC 전용 미들웨어를 거치게 둘 것.
- 대본은 **Claude와 Gemini 둘 다 정식 지원**(사용자 결정, 2026-11-12). 대본 형식(`ScriptDraft`)을 바꾸면 `app/script.py`의 프롬프트와 `app/gemini.py`의 `SCHEMA`를 함께 고칠 것.
- 영상 스타일(깔끔/감성/브이로그)은 `app/visuals.py`의 `TEMPLATES` 한 곳에서 정의한다. 화면 요소(제목 띠·장소 이름표·시작/끝 화면·썸네일)도 이 파일.
- Claude 모델은 `claude-opus-5-5`(사용자 선택). 바꾸려면 먼저 물어볼 것.
