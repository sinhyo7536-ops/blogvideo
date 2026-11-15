"""블로그 텍스트 그대로 가져오기: 문단 → 장면, 문단 바로 뒤의 사진 → 그 장면의 사진 (AI 없음)."""
from app.models import BlogPost, ImageRef
from app.textscript import MAX_CAPTION, clean_paragraph, split_caption, text_script


def _post(paras, photo_paras, title="통영 여행"):
    return BlogPost(title=title, paragraphs=paras, url="https://blog.naver.com/a/1",
                    images=[ImageRef(url=f"u{i}", para=p) for i, p in enumerate(photo_paras)])


def test_clean_paragraph():
    assert clean_paragraph("  #남파랑길 13코스(고성 배둔-거북선 마중길, 23km)  ") == "남파랑길 13코스(고성 배둔-거북선 마중길, 23km)"
    assert clean_paragraph("#통영 #거제 #여행") == ""                      # 해시태그만 → 뺌
    assert clean_paragraph("https://map.naver.com/abc") == ""
    assert clean_paragraph("...") == "" and clean_paragraph("ㅎㅎ") == "ㅎㅎ"
    assert clean_paragraph("[2번째 글] 거제 여행") == ""                    # 여러 글 경계 표시


def test_split_caption_keeps_sentences_together():
    text = "높은 아파트들이 보이기 시작하니 통영읍에 거의 다 온 것 같습니다. 뿌듯하기도 하고, 헛웃음이 나기도 합니다."
    parts = split_caption(text)
    assert parts == ["높은 아파트들이 보이기 시작하니 통영읍에 거의 다 온 것 같습니다.", "뿌듯하기도 하고, 헛웃음이 나기도 합니다."]
    long = "가나다라마바사 " * 20
    assert all(len(p) <= MAX_CAPTION for p in split_caption(long))
    assert "".join(split_caption(long)).replace(" ", "") == long.replace(" ", "")


A = "통영 버스터미널에서 아침 일찍 출발했어요."
B = "바닷가 길을 따라 한참 동안 걸었습니다."
C = "점심은 시장에서 회를 먹었어요, 정말 맛있었어요."


def test_lines_before_photos_share_those_photos_in_order():
    """블로그는 '글 몇 줄 → 그 글의 사진들' 순서. 실제 글(남파랑길): 공룡 줄, 거북선 줄 → 공룡 사진 2장, 거북선 사진 1장."""
    post = _post(["편의점에서 산 샌드위치로 기분 좋게 배를 채우며 길을 걷습니다.", "공룡의 마을답게 공룡들이 다리를 건너고",
                  "장군님 동네답게 거북선도 다리를 건넙니다."], [0, 2, 2, 2])
    s = text_script(post)
    assert [x.photo_list() for x in s.scenes] == [[0], [1, 2], [3]]


def test_line_with_its_photo_right_after():
    s = text_script(_post([A, B, C], [0, 0, 2]))
    assert s.scenes[0].photo_list() == [0, 1]
    assert s.scenes[1].photo_list() == [2] and s.scenes[2].photo_list() == [2]   # B는 C와 함께 다음 사진을


def test_long_block_with_one_photo_uses_it_near_the_end():
    lines = [A, B, C, "커피 한 잔 마시고 다시 길을 나섰어요.", "드디어 목적지에 도착했습니다, 뿌듯해요."]
    s = text_script(_post(lines, [0, 4]))
    assert [x.image_index for x in s.scenes] == [0, 0, 0, 1, 1]   # 사진 1은 가까운 마지막 두 장면에만


def test_photos_before_first_paragraph_go_to_first_scene():
    s = text_script(_post([A, B], [-1, 1]))
    assert s.scenes[0].image_index == 0 and s.scenes[1].image_index == 1


def test_first_scenes_without_photo_use_first_photo():
    s = text_script(_post([A, B, C], [2]))
    assert [x.image_index for x in s.scenes] == [0, 0, 0]


def test_long_paragraph_spreads_its_photos_over_its_scenes():
    long = "첫 문장은 꽤 길게 써서 자막 한 장면에 다 들어가지 않게 했어요. 둘째 문장도 마찬가지로 길게 이어서 썼어요. 셋째 문장이에요."
    s = text_script(_post([long], [0, 0, 0, 0]))
    assert len(s.scenes) >= 2
    assert sorted(p for x in s.scenes for p in x.photo_list()) == [0, 1, 2, 3]   # 사진 네 장이 모두 들어감
    assert all(x.photo_list() for x in s.scenes)


def test_one_photo_long_paragraph_keeps_that_photo_on_every_part():
    long = "첫 문장은 꽤 길게 써서 자막 한 장면에 다 들어가지 않게 했어요. 둘째 문장도 마찬가지로 길게 이어서 썼어요."
    s = text_script(_post([long], [0]))
    assert len(s.scenes) == 2 and all(x.image_index == 0 for x in s.scenes)


def test_many_photos_are_not_lost():
    s = text_script(_post(["짧은 글"], [0] * 10))
    assert sorted(p for x in s.scenes for p in x.photo_list()) == list(range(10))


def test_short_lines_without_photos_are_joined():
    s = text_script(_post(["출발!", "오늘도 걷습니다.", "바다가 보여요."], [2]))
    assert [x.text for x in s.scenes] == ["출발! 오늘도 걷습니다. 바다가 보여요."]
    assert s.scenes[0].image_index == 0


def test_hashtag_only_and_boundary_paragraphs_are_skipped_but_photos_kept():
    s = text_script(_post(["[1번째 글] 통영", "통영에 왔어요.", "#통영 #바다"], [0, 2, 2]))
    assert [x.text for x in s.scenes] == ["통영에 왔어요."]
    assert s.scenes[0].photo_list() == [0, 1, 2]


def test_youtube_fields_and_no_photos():
    s = text_script(_post(["글만 있어요."], [], title="[국내여행] 통영 1박 2일 | 꿀팁"))
    assert s.scenes[0].image_index is None
    assert s.yt_title.startswith("[국내여행] 통영") and "https://blog.naver.com/a/1" in s.yt_description
    assert s.intro_title == "통영 1박 2일" and s.yt_tags


# ---------- 작업 흐름 ----------
def test_text_mode_job_makes_script_without_ai(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.jobs import JobManager
    from app.main import create_app
    from tests.fakes import fake_steps, wait_stage

    monkeypatch.setenv("BLOGVIDEO_HOME", str(tmp_path / "home"))
    steps = fake_steps(key="")                       # 키가 없어도 된다
    steps.generate_script = lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI를 부르면 안 됨"))
    m = JobManager(tmp_path / "jobs", steps)
    c = TestClient(create_app(m), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
    job = c.post("/api/jobs", json={"url": "https://blog.naver.com/a/1", "mode": "text"}).json()
    s = wait_stage(m, job["id"], "ready")
    assert [x.text for x in s.script.scenes] == ["p"] and s.script.scenes[0].image_index == 0
    assert m.versions(job["id"])[0]["label"] == "블로그 글 그대로"
    r = c.post(f"/api/jobs/{job['id']}/write", json={"mode": "text"})       # '작성 방법 다시 고르기'에서도
    assert r.status_code == 200 and r.json()["stage"] == "ready"
    m.stop()


def test_short_title_drops_leading_date():
    from app.script import short_title

    assert short_title("2026. 10. 11.(일) 고성 배둔-통영 터미널(35km, 9시간 10분)").startswith("고성 배둔")
    assert short_title("2026-10-11 통영 여행") == "통영 여행"
    assert short_title("2026. 10. 11.") == "2026. 10. 11."            # 날짜뿐이면 그대로
