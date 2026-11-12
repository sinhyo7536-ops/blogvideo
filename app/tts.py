"""장면 문장 → 성우 음성 (Edge TTS)."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import edge_tts

from app.errors import UserError
from app.models import VOICE_KEYS, VideoScript
from app.paths import ffprobe_bin
from app.procutil import quiet_kwargs


@dataclass(frozen=True)
class Voice:
    name: str          # Edge TTS 목소리 이름
    label: str         # 화면에 보이는 이름
    group: str         # 한국어 / 다국어
    pitch: str = "+0Hz"


# 한국어 전용 목소리는 3개뿐이라, 높낮이를 바꾼 것과 한국어를 읽을 수 있는 다국어 목소리를 더했다.
VOICE_LIST: dict[str, Voice] = {
    "female": Voice("ko-KR-SunHiNeural", "선희 · 여자 (기본)", "한국어"),
    "female_bright": Voice("ko-KR-SunHiNeural", "선희 · 밝은 여자", "한국어", "+14Hz"),
    "female_calm": Voice("ko-KR-SunHiNeural", "선희 · 차분한 여자", "한국어", "-10Hz"),
    "male": Voice("ko-KR-InJoonNeural", "인준 · 남자 (기본)", "한국어"),
    "male_deep": Voice("ko-KR-InJoonNeural", "인준 · 낮은 남자", "한국어", "-12Hz"),
    "hyunsu": Voice("ko-KR-HyunsuMultilingualNeural", "현수 · 자연스러운 남자", "한국어"),
    "ava": Voice("en-US-AvaMultilingualNeural", "에이바 · 여자", "다국어"),
    "emma": Voice("en-US-EmmaMultilingualNeural", "엠마 · 여자", "다국어"),
    "vivienne": Voice("fr-FR-VivienneMultilingualNeural", "비비엔 · 여자", "다국어"),
    "seraphina": Voice("de-DE-SeraphinaMultilingualNeural", "세라피나 · 여자", "다국어"),
    "andrew": Voice("en-US-AndrewMultilingualNeural", "앤드루 · 남자", "다국어"),
    "brian": Voice("en-US-BrianMultilingualNeural", "브라이언 · 남자", "다국어"),
    "florian": Voice("de-DE-FlorianMultilingualNeural", "플로리안 · 남자", "다국어"),
}
assert tuple(VOICE_LIST) == VOICE_KEYS
VOICES = {k: v.name for k, v in VOICE_LIST.items()}
SPEEDS = {"slow": "-15%", "normal": "+0%", "fast": "+18%"}
PAD = 0.4
PARALLEL = 4
SAMPLE = "안녕하세요. 이 목소리로 여행 영상을 읽어 드릴게요."
FAILED = "음성을 만들지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요."
CERT_HINT = " (보안 인증서 확인에 실패했어요. 학교·회사 네트워크나 보안 프로그램 때문일 수 있어요.)"
log = logging.getLogger(__name__)


def probe_duration(path: Path) -> float:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True,
                         **quiet_kwargs())
    return float(out.stdout.strip())


async def synthesize_scene(text: str, voice: str, out: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))


def _tuning(voice: Voice, speed: str) -> dict:
    """기본값이 아닐 때만 속도·높낮이를 넘긴다."""
    extra = {}
    if SPEEDS.get(speed, "+0%") != "+0%":
        extra["rate"] = SPEEDS[speed]
    if voice.pitch != "+0Hz":
        extra["pitch"] = voice.pitch
    return extra


def synthesize_all(script: VideoScript, voice_key: str, dest: Path,
                   on_progress: Callable[[float], None] | None = None,
                   synth: Callable[..., Awaitable[None]] = synthesize_scene,
                   speed: str = "normal") -> VideoScript:
    dest.mkdir(parents=True, exist_ok=True)
    voice = VOICE_LIST.get(voice_key) or VOICE_LIST["female"]
    extra = _tuning(voice, speed)
    total = len(script.scenes)
    done = 0

    async def one(i: int, scene, gate: asyncio.Semaphore):
        nonlocal done
        out = dest / f"voice_{i:02d}.mp3"
        async with gate:
            for attempt in range(3):
                try:
                    await synth(scene.text, voice.name, out, **extra)
                    duration = await asyncio.to_thread(probe_duration, out)
                    break
                except Exception:
                    if attempt == 2:
                        raise
        done += 1
        if on_progress:
            on_progress(done / total)
        return scene.model_copy(update={"audio_path": str(out), "duration": round(duration + PAD, 3)})

    async def run_all():
        gate = asyncio.Semaphore(PARALLEL)   # 장면이 많은 긴 영상도 빨리 끝나게 몇 개씩 동시에
        return await asyncio.gather(*(one(i, sc, gate) for i, sc in enumerate(script.scenes)))

    try:
        scenes = asyncio.run(run_all())
    except Exception as e:
        log.warning("tts failed: %s: %s", type(e).__name__, e, exc_info=e)
        cert = "CERTIFICATE" in f"{type(e).__name__} {e}".upper()
        raise UserError(FAILED + (CERT_HINT if cert else "")) from e
    return script.model_copy(update={"scenes": list(scenes)})


def preview(voice_key: str, speed: str, out: Path) -> Path:
    """미리 듣기용 짧은 음성."""
    voice = VOICE_LIST.get(voice_key) or VOICE_LIST["female"]
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(synthesize_scene(SAMPLE, voice.name, out, **_tuning(voice, speed)))
    except Exception as e:
        log.warning("tts preview failed: %s: %s", type(e).__name__, e)
        raise UserError(FAILED) from e
    return out
