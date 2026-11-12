"""[임시 기능] Google Gemini 무료 API로 대본 만들기.

Claude 크레딧이 들어오기 전까지 쓰는 임시 기능이다. 나중에 뺄 때는
이 파일, tests/test_gemini.py, 그리고 'gemini' 표시가 붙은 곳(모드·설정·화면)을 지우면 된다.
"""
from __future__ import annotations

import logging

from google import genai
from google.genai import errors, types

from app.errors import UserError
from app.models import BlogPost, Scene, VideoScript
from app.script import SYSTEM, ScriptDraft, _user_message, normalize_script

log = logging.getLogger(__name__)

# 무료 사용분이 있는 모델 (Google 가격표 기준). 앞의 모델이 없으면 다음 것으로.
MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

NO_KEY = "Gemini API 키가 없어요. 설정에서 Gemini API 키를 넣어 주세요."
BAD_KEY = "Gemini API 키를 확인해 주세요. 설정에서 다시 넣어 주세요."
QUOTA = "Gemini 무료 사용량(하루 한도)을 다 썼어요. 내일 다시 하거나 'AI 없이(무료)'로 만들어 주세요."
BUSY = "Gemini 서버가 바빠요. 잠시 뒤 다시 시도해 주세요."
FAILED = "대본을 만들지 못했어요. 다시 시도해 주세요."


def _client(api_key: str):
    return genai.Client(api_key=api_key)


def generate_script_gemini(post: BlogPost, api_key: str, client=None) -> VideoScript:
    if client is None:
        if not api_key:
            raise UserError(NO_KEY)
        client = _client(api_key)
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_json_schema=ScriptDraft.model_json_schema(),
    )
    models = list(MODELS)
    busy = False
    attempts = 0
    while models and attempts < 2:
        model = models[0]
        try:
            resp = client.models.generate_content(model=model, contents=_user_message(post), config=cfg)
        except errors.ClientError as e:
            log.warning("gemini %s client error %s: %s", model, e.code, e)
            if e.code == 404:
                models.pop(0)
                continue
            if e.code == 429:
                raise UserError(QUOTA) from e
            if e.code in (401, 403) or "api key" in str(e).lower():
                raise UserError(BAD_KEY) from e
            raise UserError(FAILED) from e
        except errors.ServerError as e:
            log.warning("gemini %s server error %s", model, e.code)
            busy = True
            attempts += 1
            continue
        except Exception as e:  # 네트워크 등
            from app.netsetup import describe_network_error

            raise UserError(describe_network_error(e)) from e
        attempts += 1
        try:
            draft = ScriptDraft.model_validate_json(resp.text or "")
        except ValueError:
            log.warning("gemini returned invalid JSON")
            continue
        raw = VideoScript(scenes=[Scene(text=s.text, image_index=s.image_index) for s in draft.scenes],
                          yt_title=draft.yt_title, yt_description=draft.yt_description, yt_tags=draft.yt_tags)
        result = normalize_script(raw, len(post.images))
        if result.scenes:
            return result
    raise UserError(BUSY if busy else FAILED)
