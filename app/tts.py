"""장면 문장 → 성우 음성 (Edge TTS)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Awaitable, Callable

import logging

import edge_tts

from app.errors import UserError
from app.models import VideoScript
from app.paths import ffprobe_bin
from app.procutil import quiet_kwargs

VOICES = {"female": "ko-KR-SunHiNeural", "male": "ko-KR-InJoonNeural"}
PAD = 0.4
FAILED = "음성을 만들지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요."
CERT_HINT = " (보안 인증서 확인에 실패했어요. 학교·회사 네트워크나 보안 프로그램 때문일 수 있어요.)"
log = logging.getLogger(__name__)


def probe_duration(path: Path) -> float:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True,
                         **quiet_kwargs())
    return float(out.stdout.strip())


async def synthesize_scene(text: str, voice: str, out: Path) -> None:
    await edge_tts.Communicate(text, voice).save(str(out))


def synthesize_all(script: VideoScript, voice_key: str, dest: Path,
                   on_progress: Callable[[float], None] | None = None,
                   synth: Callable[[str, str, Path], Awaitable[None]] = synthesize_scene) -> VideoScript:
    dest.mkdir(parents=True, exist_ok=True)
    voice = VOICES[voice_key]
    scenes = []
    total = len(script.scenes)
    for i, scene in enumerate(script.scenes):
        out = dest / f"voice_{i:02d}.mp3"
        for attempt in range(3):
            try:
                asyncio.run(synth(scene.text, voice, out))
                duration = probe_duration(out)
                break
            except Exception as e:
                if attempt == 2:
                    log.warning("tts failed: %s: %s", type(e).__name__, e, exc_info=e)
                    cert = "CERTIFICATE" in f"{type(e).__name__} {e}".upper()
                    raise UserError(FAILED + (CERT_HINT if cert else "")) from e
        scenes.append(scene.model_copy(update={"audio_path": str(out), "duration": round(duration + PAD, 3)}))
        if on_progress:
            on_progress((i + 1) / total)
    return script.model_copy(update={"scenes": scenes})
