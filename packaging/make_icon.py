"""앱 아이콘 만들기 (스튜디오 디자인, 2026-11-15): 짙은 남색 바탕, 파란 말풍선(블로그 글) 안의 재생 버튼(영상),
테라코타 반짝임(AI). 실행: .venv\\Scripts\\python packaging\\make_icon.py
결과: app/web/icon.png (앱 창·탭), packaging/icon.ico (설치 파일·바탕화면 아이콘)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024                      # 크게 그려서 줄인다 (가장자리를 매끄럽게)
NAVY, BLUE, WHITE, CLAY = (19, 33, 58), (47, 107, 255), (255, 255, 255), (224, 122, 95)


def k(v: float) -> int:      # 56칸 설계도 → 1024 픽셀
    return round(v * S / 56)


def draw() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=k(14), fill=NAVY)
    # 말풍선: 둥근 사각형 + 왼쪽 아래 꼬리
    d.rounded_rectangle([k(9), k(15), k(43), k(37)], radius=k(4), fill=BLUE)
    d.polygon([(k(17), k(36)), (k(25), k(36)), (k(17), k(43))], fill=BLUE)
    # 재생 버튼
    d.polygon([(k(22.5), k(20.5)), (k(22.5), k(31.5)), (k(32), k(26))], fill=WHITE)
    # 반짝임 (네 꼭짓점 별)
    cx, cy, r, w = k(44), k(14), k(6.2), k(1.7)
    d.polygon([(cx, cy - r), (cx + w, cy - w), (cx + r, cy), (cx + w, cy + w),
               (cx, cy + r), (cx - w, cy + w), (cx - r, cy), (cx - w, cy - w)], fill=CLAY)
    return im


def main() -> None:
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "app" / "web" / "icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "packaging" / "icon.ico", sizes=sizes)
    print("icon.png, icon.ico 저장")


if __name__ == "__main__":
    main()
