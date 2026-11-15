"""AI 대본도 장면의 사진을 그 장면이 나온 문단 근처 사진으로 고정한다. 사진이 많아도 AI가 모두 본다."""
import io

from PIL import Image

from app.models import BlogPost, ImageRef
from app.script import SYSTEM, MAX_PHOTOS, ScriptDraft, SceneDraft, draft_to_script, photos_for_ai

URL = "https://blog.naver.com/a/1"


def _post(n_para=6, photo_paras=(0, 1, 2, 3, 4, 5)):
    return BlogPost(title="통영", paragraphs=[f"문단 {k}" for k in range(n_para)], url=URL,
                    images=[ImageRef(url=f"u{i}", para=p) for i, p in enumerate(photo_paras)])


def _draft(*scenes):
    return ScriptDraft(scenes=list(scenes), yt_title="t", yt_description="d", yt_tags=[])


def test_prompt_asks_for_source_paragraphs():
    assert "paragraphs" in SYSTEM and "문단 바로 뒤" in SYSTEM


def test_photo_far_from_its_paragraph_is_replaced_by_a_nearby_one():
    # 장면은 문단 4(1부터 센 번호 5)에서 나왔는데 AI가 문단 0 뒤의 사진 0을 골랐다 → 문단 4 근처 사진으로
    s = draft_to_script(_draft(SceneDraft(text="다섯째", image_index=0, paragraphs=[5])), _post())
    assert s.scenes[0].image_index == 4


def test_opening_hook_keeps_the_ai_choice():
    s = draft_to_script(_draft(SceneDraft(text="이 바다, 보이세요?", image_index=5, paragraphs=[1], section="opening")),
                        _post())
    assert s.scenes[0].image_index == 5


def test_nearby_choice_is_kept():
    s = draft_to_script(_draft(SceneDraft(text="다섯째", image_index=5, paragraphs=[5])), _post())
    assert s.scenes[0].image_index == 5                  # 바로 다음 문단 뒤 사진도 근처로 인정


def test_unused_nearby_photo_is_preferred():
    post = _post(photo_paras=(0, 4, 4, 5))
    s = draft_to_script(_draft(SceneDraft(text="a", image_index=1, paragraphs=[5]),
                               SceneDraft(text="b", image_index=0, paragraphs=[5])), post)
    assert [x.image_index for x in s.scenes] == [1, 2]


def test_without_paragraphs_or_nearby_photos_ai_choice_stays():
    s = draft_to_script(_draft(SceneDraft(text="a", image_index=3)), _post())
    assert s.scenes[0].image_index == 3
    s = draft_to_script(_draft(SceneDraft(text="a", image_index=3, paragraphs=[99])), _post())
    assert s.scenes[0].image_index == 3


def test_many_photos_are_all_shown_but_smaller(tmp_path):
    imgs = []
    for i in range(45):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1600, 1200), (i * 5, 80, 120)).save(p)
        imgs.append(ImageRef(url=f"u{i}", local_path=str(p)))
    post = BlogPost(title="t", paragraphs=["p"], url=URL, images=imgs)
    photos = photos_for_ai(post)
    assert [i for i, _ in photos] == list(range(45)) and MAX_PHOTOS >= 45
    assert max(Image.open(io.BytesIO(photos[0][1])).size) == 512        # 많으면 작게 (비용·크기 제한)
    few = photos_for_ai(post.model_copy(update={"images": imgs[:5]}))
    assert max(Image.open(io.BytesIO(few[0][1])).size) == 768
