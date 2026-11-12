from PIL import Image

from app.captions import render_caption_png


def test_caption_size_and_mode(tmp_path):
    im = Image.open(render_caption_png("안녕하세요", 1920, 1080, tmp_path / "c.png"))
    assert im.size == (1920, 1080) and im.mode == "RGBA"
    assert im.getpixel((10, 10))[3] == 0  # 위쪽은 투명


def test_caption_wraps_long_text(tmp_path):
    im = Image.open(render_caption_png("아주 " * 60, 1080, 1920, tmp_path / "c.png"))
    bbox = im.getbbox()
    assert bbox[0] >= 0 and bbox[2] <= 1080 and bbox[3] <= 1920
    # 텍스트 띠가 화면 높이의 절반을 넘지 않음(최대 3줄 + 축소)
    assert bbox[3] - bbox[1] < 1920 / 2


def test_caption_long_single_word_fits(tmp_path):
    im = Image.open(render_caption_png("가" * 120, 1920, 1080, tmp_path / "c.png"))
    assert im.getbbox()[2] <= 1920


def test_caption_special_chars(tmp_path):
    p = render_caption_png('그는 "50% 할인: 오늘만!" 이라고 했다 😀', 1920, 1080, tmp_path / "c.png")
    assert Image.open(p).getbbox() is not None


def test_vertical_caption_sits_above_bottom_ui(tmp_path):
    im = Image.open(render_caption_png("세로 영상 자막", 1080, 1920, tmp_path / "c.png"))
    assert im.getbbox()[3] <= int(1920 * 0.80)
