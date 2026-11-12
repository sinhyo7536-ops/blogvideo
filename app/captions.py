"""자막 PNG 생성 (Pillow). FFmpeg drawtext의 이스케이프 문제를 피하려고 이미지로 그린다."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.paths import resource_path

FONT = "assets/fonts/NotoSansKR-Bold.ttf"
MAX_LINES = 3
MIN_SIZE = 40
_EMOJI = re.compile("[\U0001F000-\U0010FFFF\uFE0F\u200D]")


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(resource_path(FONT)), size)


@lru_cache(maxsize=1)
def _notdef() -> bytes:
    f = _font(48)
    return bytes(f.getmask("\U0010FFFD"))


@lru_cache(maxsize=4096)
def _has_glyph(ch: str) -> bool:
    if ch.isspace():
        return True
    return bytes(_font(48).getmask(ch)) != _notdef()


def supported_text(text: str) -> str:
    """폰트에 없는 글자(이모지 등)를 지워 영상에 네모(□)가 나오지 않게 한다."""
    kept = "".join(ch for ch in _EMOJI.sub("", text) if _has_glyph(ch))
    return " ".join(kept.split())


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        cand = f"{cur} {word}".strip()
        if font.getlength(cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while font.getlength(word) > max_w:  # 단어 하나가 너무 길면 글자 단위로 자름
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > max_w:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    return lines


def _layout(text: str, max_w: int, size: int, min_size: int = MIN_SIZE) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    while True:
        font = _font(size)
        lines = _wrap(text, font, max_w)
        if len(lines) <= MAX_LINES or size <= min_size:
            if len(lines) > MAX_LINES:
                lines = lines[:MAX_LINES]
                lines[-1] = lines[-1][:-1] + "…"
            return font, lines
        size -= max(2, size // 18)


def render_caption_png(text: str, width: int, height: int, out: Path) -> Path:
    text = supported_text(text)
    vertical = height > width
    k = min(width, height) / 1080  # 화질(720p/1080p/1440p)에 맞춰 비율 유지
    base = round((72 if vertical else 64) * k)
    max_w = int(width * 0.88)
    font, lines = _layout(text, max_w - round(40 * k), base, round(MIN_SIZE * k))
    size = font.size
    line_h = int(size * 1.35)
    pad = int(size * 0.45)
    box_h = line_h * len(lines) + pad * 2
    bottom = int(height * (0.78 if vertical else 0.93))
    top = bottom - box_h

    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if lines:
        widest = max(font.getlength(l) for l in lines)
        box_w = min(max_w, int(widest) + pad * 2 + round(20 * k))
        left = (width - box_w) // 2
        d.rounded_rectangle([left, top, left + box_w, bottom], radius=int(size * 0.3), fill=(0, 0, 0, 160))
        for i, line in enumerate(lines):
            y = top + pad + i * line_h + line_h // 2
            d.text((width // 2, y), line, font=font, fill="white", anchor="mm",
                   stroke_width=max(2, round(4 * k)), stroke_fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out
