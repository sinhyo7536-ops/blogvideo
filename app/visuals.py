"""영상 스타일(템플릿)과 화면 요소: 제목 띠, 장소 이름표, 시작·끝 화면, 썸네일 (Pillow)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from app.captions import _font, _wrap, supported_text

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    accent: RGB            # 제목 띠·강조 색
    accent_text: RGB       # 강조 색 위의 글자 색
    caption: str           # box | plain | highlight
    transitions: tuple[str, ...]   # FFmpeg xfade 종류 (장면마다 돌아가며 사용)
    transition_sec: float


TEMPLATES: dict[str, Template] = {
    "clean": Template("clean", "깔끔", (31, 111, 235), (255, 255, 255), "box",
                      ("fade", "smoothleft", "fade", "smoothright"), 0.5),
    "mood": Template("mood", "감성", (244, 235, 220), (72, 52, 40), "plain",
                     ("fade", "fadeblack", "fade", "dissolve"), 0.8),
    "vlog": Template("vlog", "브이로그", (255, 214, 10), (20, 20, 20), "highlight",
                     ("slideleft", "circleopen", "slideup", "smoothright", "slidedown"), 0.4),
}
SOFT = ("fade",)
THUMB_SIZE = (1280, 720)


def template(key: str) -> Template:
    return TEMPLATES.get(key, TEMPLATES["clean"])


def _fit_lines(text: str, max_w: int, size: int, min_size: int, max_lines: int):
    """글자 크기를 줄여 가며 max_lines 줄 안에 맞춘다."""
    while True:
        font = _font(size)
        lines = _wrap(text, font, max_w)
        if len(lines) <= max_lines or size <= min_size:
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines[-1] = lines[-1][:-1] + "…"
            return font, lines
        size -= max(2, size // 14)


def _one_line(text: str, max_w: int, size: int, min_size: int):
    font, lines = _fit_lines(text, max_w, size, min_size, 1)
    return font, (lines[0] if lines else "")


def _pin(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    """위치 핀 모양 (폰트에 이모지가 없어서 직접 그린다)."""
    d.ellipse([cx - r, cy - r * 1.25, cx + r, cy + r * 0.75], fill=fill)
    d.polygon([(cx - r * 0.8, cy + r * 0.25), (cx + r * 0.8, cy + r * 0.25), (cx, cy + r * 1.7)], fill=fill)
    d.ellipse([cx - r * 0.38, cy - r * 0.63, cx + r * 0.38, cy + r * 0.13], fill=(255, 255, 255, 255))


def render_overlay_png(title: str, place: str, width: int, height: int, out: Path,
                       template_key: str = "clean") -> Path:
    """장면 위에 계속 떠 있는 요소: 왼쪽 위 제목 띠 + 그 아래 장소 이름표."""
    tpl = template(template_key)
    title, place = supported_text(title), supported_text(place)
    k = min(width, height) / 1080
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    top = round(height * (0.09 if height > width else 0.055))
    bar_h = round(76 * k)
    y_next = top
    if title:
        font, line = _one_line(title, int(width * 0.6), round(40 * k), round(26 * k))
        pad = round(30 * k)
        bar_w = int(font.getlength(line)) + pad * 2
        slant = round(bar_h * 0.45)
        d.polygon([(0, top), (bar_w + slant, top), (bar_w, top + bar_h), (0, top + bar_h)], fill=tpl.accent + (240,))
        d.text((pad, top + bar_h // 2), line, font=font, fill=tpl.accent_text, anchor="lm")
        y_next = top + bar_h + round(16 * k)
    if place:
        font, line = _one_line(place, int(width * 0.55), round(36 * k), round(24 * k))
        h = round(62 * k)
        r = round(13 * k)
        left = round(24 * k)
        w = int(font.getlength(line)) + round(28 * k) * 2 + r * 3
        d.rounded_rectangle([left, y_next, left + w, y_next + h], radius=h // 2, fill=(0, 0, 0, 170))
        _pin(d, left + round(28 * k) + r, y_next + h / 2 - r * 0.2, r, tpl.accent + (255,))
        d.text((left + round(28 * k) + r * 3, y_next + h // 2), line, font=font, fill="white", anchor="lm")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def render_card_png(kind: str, title: str, sub: str, tag: str, width: int, height: int, out: Path,
                    template_key: str = "clean") -> Path:
    """시작(intro)·끝(outro) 화면에 올리는 글자. 배경(흐린 사진)은 영상 쪽에서 깐다."""
    tpl = template(template_key)
    title, sub, tag = supported_text(title), supported_text(sub), supported_text(tag)
    k = min(width, height) / 1080
    vertical = height > width
    im = Image.new("RGBA", (width, height), (0, 0, 0, 110))  # 사진을 어둡게 눌러 글자가 잘 보이게
    d = ImageDraw.Draw(im)
    max_w = int(width * (0.86 if vertical else 0.8))
    font, lines = _fit_lines(title, max_w, round((120 if kind == "intro" else 96) * k), round(54 * k), 3)
    line_h = int(font.size * 1.25)
    sub_font, sub_line = _one_line(sub, max_w, round(48 * k), round(30 * k)) if sub else (None, "")
    tag_font, tag_line = _one_line(tag, max_w, round(40 * k), round(26 * k)) if tag else (None, "")
    gap = round(34 * k)
    total = line_h * len(lines) + (gap + sub_font.size if sub_line else 0) + (gap * 2 + round(70 * k) if tag_line else 0)
    y = (height - total) // 2
    stroke = max(2, round(5 * k))
    # 제목 위 강조 선
    bar_w, bar_t = round(120 * k), max(4, round(10 * k))
    d.rounded_rectangle([width // 2 - bar_w // 2, y - gap - bar_t, width // 2 + bar_w // 2, y - gap],
                        radius=bar_t // 2, fill=tpl.accent + (255,))
    for line in lines:
        d.text((width // 2, y + line_h // 2), line, font=font, fill="white", anchor="mm",
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += line_h
    if sub_line:
        y += gap
        d.text((width // 2, y + sub_font.size // 2), sub_line, font=sub_font, fill=(240, 240, 240, 255), anchor="mm",
               stroke_width=max(1, stroke // 2), stroke_fill=(0, 0, 0, 255))
        y += sub_font.size
    if tag_line:
        y += gap * 2
        h = round(70 * k)
        w = int(tag_font.getlength(tag_line)) + round(44 * k) * 2
        d.rounded_rectangle([width // 2 - w // 2, y, width // 2 + w // 2, y + h], radius=h // 2,
                            fill=tpl.accent + (255,))
        d.text((width // 2, y + h // 2), tag_line, font=tag_font, fill=tpl.accent_text, anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def blurred_bg(src: str, out: Path, size: tuple[int, int]) -> Path:
    """시작·끝 화면 배경: 사진을 꽉 채워 자른 뒤 흐리게."""
    with Image.open(src) as im:
        img = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), size, Image.LANCZOS)
    img.filter(ImageFilter.GaussianBlur(max(4, min(size) // 60))).save(out, quality=90)
    return out


def render_thumbnail(photo: str | None, title: str, channel: str, out: Path, template_key: str = "clean") -> Path:
    """유튜브 썸네일 (1280x720 JPG): 사진 + 큰 제목."""
    tpl = template(template_key)
    title, channel = supported_text(title), supported_text(channel)
    w, h = THUMB_SIZE
    base = Image.new("RGB", (w, h), (30, 30, 40))
    if photo:
        try:
            with Image.open(photo) as im:
                base = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (w, h), Image.LANCZOS)
        except OSError:
            pass
    im = base.convert("RGBA")
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(h):  # 아래로 갈수록 어둡게
        sd.line([(0, y), (w, y)], fill=(0, 0, 0, int(40 + 170 * (y / h) ** 1.6)))
    im = Image.alpha_composite(im, shade)
    d = ImageDraw.Draw(im)
    font, lines = _fit_lines(title, int(w * 0.9), 132, 64, 3)
    line_h = int(font.size * 1.18)
    y = h - 54 - line_h * len(lines)
    accent_line = tpl.accent if tpl.key != "clean" else (255, 214, 10)
    for i, line in enumerate(lines):
        last = i == len(lines) - 1 and len(lines) > 1
        d.text((w // 2, y + line_h // 2), line, font=font, fill=accent_line if last else "white", anchor="mm",
               stroke_width=10, stroke_fill="black")
        y += line_h
    if channel:
        cf, cl = _one_line(channel, int(w * 0.5), 38, 26)
        cw = int(cf.getlength(cl)) + 56
        d.rounded_rectangle([36, 32, 36 + cw, 32 + 64], radius=32, fill=tpl.accent + (255,))
        d.text((36 + cw // 2, 64), cl, font=cf, fill=tpl.accent_text, anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out, quality=92)
    return out
