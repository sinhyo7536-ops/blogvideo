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
- 새 기능·버그 수정은 실패하는 테스트를 먼저 쓰고(TDD) 고친다. 외부 API(Claude, Edge TTS, 네이버, 유튜브)는 테스트에서 가짜로 대체(`tests/fakes.py`).
- `packaging/`에 `__init__.py` 금지(파이썬 `packaging` 라이브러리를 가림).
- `packaging/installer.iss`는 UTF-8 **BOM**으로 저장.
- 확대 효과에 FFmpeg `zoompan` 사용 금지(떨림). `render.py`의 2배 크기 scale→crop→축소 방식을 유지.
- 외부 명령은 `subprocess.run([...])` 리스트 인자 + `procutil.quiet_kwargs()`.
- 서버는 `127.0.0.1` 전용. 새 API도 `main.py`의 이 PC 전용 미들웨어를 거치게 둘 것.
- AI는 **Claude만** 쓴다(사용자 결정, 2026-11-15: 제미나이 제거).
- 처음 화면 순서(사용자 결정, 2026-11-15): ① **블로그 텍스트 그대로 가져오기**(`app/textscript.py`, AI 없음: 문단 → 장면, 블로그에서 그 글 뒤에 있던 사진 → 그 장면) ② **AI로 대본 만들기**. 'AI 없이 직접 작성하기' 버튼은 없앴다(빈 대본 `manual` 모드는 예전 프로젝트용으로 서버에만 남김).
- 장면의 사진은 그 장면이 나온 문단 근처 사진이어야 한다: AI 대본은 장면마다 `paragraphs`(1부터)를 받고 `script.pin_photo` 가 멀리 떨어진 사진을 근처 사진으로 바꾼다(오프닝 장면은 예외).
- 영상 스타일(깔끔/감성/브이로그/시네마/에세이)과 영상 프리셋 4종(`PRESETS`)은 `app/visuals.py` 한 곳에서 정의한다. 화면 요소(제목 띠·장소 이름표·시작/끝 화면·썸네일)도 이 파일.
- AI가 정할 수 있는 연출 항목을 늘리면 `app/models.py`(값 목록), `app/director.py`(프롬프트·`SCHEMA`·`apply`), `app/render.py`(영상 반영), `app.js`(표시) 네 곳을 함께 고칠 것.
- 영상 길이·장면 수에 제한을 두지 말 것(사용자 결정: 블로그 내용이 전부 들어가야 함). 길이는 사용자가 편집 화면에서 장면을 지워 조절한다.
- Claude 모델 ID는 `app/ai/models.py` 에만 둔다(작업별 기본값 `TASKS`, 고를 수 있는 목록 `CHOICES`). 다른 코드는 `ask_claude(..., task=...)` 로 작업 이름만 넘긴다. 기본값을 바꾸려면 먼저 물어볼 것(사용자 결정: 대본 Opus 5.5, 분석·사진·재작성 Sonnet 5.5, 검수·유튜브 문구 Haiku 5.5). `tests/test_ai_models.py` 가 다른 파일에 모델 ID가 섞이면 실패한다.
- 다시 만들 때 재사용: 장면 조각·배경 사진은 `render._digest(...)` 지문 이름으로 `render/cache/`, 목소리는 `tts.voice_file_key` 이름. 영상에 영향을 주는 값을 새로 만들면 지문에 꼭 넣을 것(빠지면 예전 조각이 잘못 재사용됨).
- 화면 디자인(사용자 결정, 2026-11-15, 시안 1 '스튜디오 편집실'): 짙은 남색 바탕, **파랑 = 사용자의 기본 동작, 테라코타 ✦(`class="ai"`) = AI가 하는 일**. 색은 `app.css` 끝의 스튜디오 블록 변수(`--bg`, `--ai` …)로만 바꾼다. 넓은 창(1100px 이상)은 왼쪽 작업 / 오른쪽 미리보기·타임라인(`.studio`). 아이콘은 `packaging/make_icon.py` 로 다시 만든다.
- 고도화 계획과 진행 순서는 `docs/고도화계획.md`. 단계마다 구현·파일·테스트·오류·남은 작업·다음 계획을 보고한다.
