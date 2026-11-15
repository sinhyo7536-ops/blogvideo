"""작업별 Claude 모델 설정.

모델 ID는 이 파일의 목록(CHOICES)과 기본값(TASKS)에만 있다. 사용자가 설정 화면에서 바꾸면
config.json 의 "models" 항목에 {작업: 모델 ID} 로 저장된다. 다른 코드는 model_for(작업)만 부른다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import config


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    price: str            # 100만 토큰당 입력/출력 (2026-11 Anthropic 공식 요금)
    fallback: bool        # 서버 쪽 대체 모델(server-side fallback)을 쓸 수 있는지


CHOICES: dict[str, Choice] = {
    "claude-opus-5-5": Choice("claude-opus-5-5", "Opus 5.5 · 가장 좋은 품질", "$4 / $20", True),
    "claude-sonnet-5-5": Choice("claude-sonnet-5-5", "Sonnet 5.5 · 품질과 비용의 균형", "$2 / $10", True),
    "claude-haiku-5-5": Choice("claude-haiku-5-5", "Haiku 5.5 · 빠르고 아주 저렴", "$0.10 / $0.50", False),
}


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    help: str
    default: str
    effort: str = "medium"


TASKS: dict[str, Task] = {
    "script": Task("script", "여행 대본 쓰기", "블로그 글과 사진으로 영상 대본 전체를 써요. 결과물의 핵심이에요.",
                   "claude-opus-5-5"),
    "analysis": Task("analysis", "여행 분석", "글과 사진을 읽고 일정·장소·후기를 정리해요.", "claude-sonnet-5-5"),
    "photos": Task("photos", "사진 분류·매칭", "사진을 보고 종류를 나누고 대본 문장에 맞는 사진을 골라요.",
                   "claude-sonnet-5-5"),
    "rewrite": Task("rewrite", "문단 고쳐 쓰기·연출", "고르신 문단을 다시 쓰거나 연출 요청을 영상 설정으로 바꿔요.",
                    "claude-sonnet-5-5"),
    "review": Task("review", "맞춤법·영상 검수", "맞춤법과 문맥, 완성 영상을 점검해요.", "claude-haiku-5-5", "low"),
    "metadata": Task("metadata", "유튜브 제목·설명·태그", "유튜브에 올릴 제목·설명·태그·썸네일 문구를 만들어요.",
                     "claude-haiku-5-5", "low"),
}


def saved() -> dict[str, str]:
    """설정에 저장된 {작업: 모델 ID}. 목록에 없는 값은 버린다."""
    raw = config.load_config().get("models") or {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in TASKS and v in CHOICES}


def model_for(task: str) -> str:
    return saved().get(task) or TASKS[task].default


def effort_for(task: str) -> str:
    return TASKS[task].effort


def supports_fallback(model: str) -> bool:
    c = CHOICES.get(model)
    return bool(c and c.fallback)


def current() -> dict[str, str]:
    """모든 작업의 지금 모델 (저장값이 없으면 기본값)."""
    chosen = saved()
    return {k: chosen.get(k, t.default) for k, t in TASKS.items()}


def save(choices: dict[str, str]) -> dict[str, str]:
    """화면에서 고른 값을 저장한다. 기본값과 같은 것은 저장하지 않는다(나중에 기본값을 바꾸면 따라가게)."""
    data = config.load_config()
    keep = {}
    for k, v in (choices or {}).items():
        if k in TASKS and v in CHOICES and v != TASKS[k].default:
            keep[k] = v
    data["models"] = keep
    config.save_config(data)
    return current()


def catalog() -> dict:
    """설정 화면용 목록."""
    return {
        "choices": [{"id": c.id, "label": c.label, "price": c.price} for c in CHOICES.values()],
        "tasks": [{"key": t.key, "label": t.label, "help": t.help, "default": t.default} for t in TASKS.values()],
        "current": current(),
    }


def check(api_key: str, client=None) -> list[dict]:
    """지금 설정된 모델마다 아주 짧게 물어 보고 쓸 수 있는지 알려 준다(연결 확인 버튼)."""
    import anthropic

    from app.errors import UserError
    from app.script import KEY_MSG, NO_CREDIT, OFFLINE

    if client is None:
        if not api_key:
            raise UserError(KEY_MSG)
        client = anthropic.Anthropic(api_key=api_key, timeout=60, max_retries=1)
    results = []
    for model in dict.fromkeys(current().values()):
        tasks = [t.label for k, t in TASKS.items() if current()[k] == model]
        try:
            client.messages.create(model=model, max_tokens=512, output_config={"effort": "low"},
                                   messages=[{"role": "user", "content": "연결 확인입니다. '확인'이라고만 답해 주세요."}])
            ok, message = True, "사용할 수 있어요"
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            ok, message = False, KEY_MSG
        except anthropic.NotFoundError:
            ok, message = False, "이 키로는 이 모델을 쓸 수 없어요. 다른 모델을 골라 주세요."
        except anthropic.BadRequestError as e:
            text = str(e).lower()
            ok, message = False, NO_CREDIT if ("credit" in text or "billing" in text) else "요청이 거절됐어요."
        except anthropic.APIConnectionError:
            ok, message = False, OFFLINE
        except anthropic.APIStatusError as e:
            ok, message = False, f"AI 서버 오류({e.status_code})예요. 잠시 뒤 다시 해 주세요."
        results.append({"model": model, "label": CHOICES[model].label, "tasks": tasks, "ok": ok, "message": message})
    return results
