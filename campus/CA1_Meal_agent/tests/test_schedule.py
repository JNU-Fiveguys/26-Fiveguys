"""시간표(F3 오늘 수업) → 식사 공강 · 강의실 코드 → 건물."""

from meal.schedule import auto_codes, parse_classes_text, room_building, slots_from_classes

B = {"공과대 7호관": (35.1, 126.9), "공과대 2호관": (35.2, 126.9), "공과대 2호관 별관": (35.3, 126.9),
     "자연과학대학 1호관": (35.4, 126.9), "진리관": (35.5, 126.9), "생활관 7호관 이룰학사": (35.6, 126.9)}


def test_auto_codes_from_names():
    c = auto_codes(B)
    assert c["공7"] == "공과대 7호관" and c["공2"] == "공과대 2호관" and c["자1"] == "자연과학대학 1호관"
    assert "생7" not in c and not any(v == "공과대 2호관 별관" for v in c.values())


def test_room_building():
    codes = {**auto_codes(B), "진": "진리관"}
    assert room_building("공7-223", B, codes) == "공과대 7호관"
    assert room_building("공2 100", B, codes) == "공과대 2호관"
    assert room_building("진리관 101", B, codes) == "진리관"            # 이름이 그대로 들어 있음
    assert room_building("공2호관 별관", B, codes) is None               # 모르는 모양
    assert room_building("생활100", {**B, "생활과학대학": (0, 0)}, {**codes, "생활": "생활과학대학"}) == "생활과학대학"   # 줄표 없는 호실
    assert room_building("", B, codes) is None and room_building("경9-101", B, codes) is None


def test_slots_from_classes_uses_rooms_and_skips_canceled():
    classes = [
        {"name": "운영체제", "start": "09:00", "end": "10:15", "room": "공7-223", "state": "scheduled"},
        {"name": "자료구조", "start": "10:30", "end": "11:45", "room": "공2-100", "state": "canceled"},   # 휴강
        {"name": "확률", "start": "13:00", "end": "14:15", "room": "자1-101", "state": "scheduled"},
    ]
    s = slots_from_classes(classes, B)
    assert s[0] == {"start": "10:15", "end": "13:00", "minutes": 165, "label": "10:15~13:00 · 165분",
                    "from": "공과대 7호관", "to": "자연과학대학 1호관", "lastOfDay": False}
    assert s[-1]["lastOfDay"] and s[-1]["from"] == "자연과학대학 1호관" and s[-1]["to"] is None


def test_parse_classes_text():
    assert parse_classes_text("9:00-10:15 공7-223 운영체제\n잘못된 줄\n13:00~14:15 자1-101 확률 휴강") == [
        {"start": "09:00", "end": "10:15", "room": "공7-223", "name": "운영체제", "state": "scheduled"},
        {"start": "13:00", "end": "14:15", "room": "자1-101", "name": "확률", "state": "canceled"}]


def test_run_schedule_sources():
    from meal.preview import run_schedule

    d = run_schedule("09:00-10:15 공7-223 운영체제\n13:00-14:15 자1-101 확률", B, {})
    assert d["source"] == "manual" and d["slots"][0]["from"] == "공과대 7호관"
    got = []
    classes = [{"name": "운영체제", "start": "09:00", "end": "10:15", "room": "공7-223", "state": "scheduled"},
               {"name": "확률", "start": "12:00", "end": "13:15", "room": "자1-101", "state": "canceled"}]
    d = run_schedule("", B, {}, weekday=0, classes_for=lambda day: got.append(day) or classes)
    assert d["source"] == "f3" and d["weekday"] == 0 and got[0].weekday() == 0 and d["date"] == got[0].isoformat()
    assert d["text"].splitlines()[1].endswith("휴강") and d["slots"][0]["lastOfDay"]   # 휴강을 빼면 운영체제가 마지막
    assert run_schedule("", B, {}, classes_for=lambda day: None)["source"] == "none"
    d = run_schedule("", B, {}, classes_for=lambda day: [], has_times=lambda: False)
    assert d["source"] == "f3" and d["noTimes"]               # 과목만 있고 수업 시간이 없음


def test_classes_from_events():
    from meal.schedule import classes_from_events

    ev = [{"title": "확률", "start": "2026-11-16T13:00:00", "end": "2026-11-16T14:15:00",
           "extendedProps": {"kind": "class", "room": "자1-101", "state": "scheduled"}},
          {"title": "과제", "start": "2026-11-16T09:00:00", "end": "2026-11-16T09:00:00", "extendedProps": {"kind": "deadline"}},
          {"title": "운영체제", "start": "2026-11-16T09:00:00", "end": "2026-11-16T10:15:00",
           "extendedProps": {"kind": "class", "room": "공7-223", "state": "canceled"}}]
    assert classes_from_events(ev) == [
        {"name": "운영체제", "start": "09:00", "end": "10:15", "room": "공7-223", "state": "canceled"},
        {"name": "확률", "start": "13:00", "end": "14:15", "room": "자1-101", "state": "scheduled"}]


def test_run_schedule_classes_have_buildings():
    from meal.preview import run_schedule

    d = run_schedule("09:00-10:15 공7-223 운영체제\n13:00-14:15 경9-101 모름", B, {})
    assert [c["building"] for c in d["classes"]] == ["공과대 7호관", None]   # 빈 시간을 눌렀을 때 출발·도착 건물
