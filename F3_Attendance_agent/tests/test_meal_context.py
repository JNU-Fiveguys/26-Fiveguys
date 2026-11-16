"""개인 데이터 대신 합성 시간표로 읽기 전용 CLI·식사 시간 블록 계약을 검증한다."""
import hashlib
import json
import sqlite3
from datetime import datetime

import pytest

from attendance import config as C, meal_context, service, store
from attendance.__main__ import main
from conftest import F1_ROWS, make_eclass, make_f1

SEMESTER = "2026-2"
SECRET = "PRIVATE_STUDENT_COURSE_MEMO_12345678"


def seed(con, meetings=None, *, cid="private-course-id", excluded=False, valid_from=""):
    stamp = "2026-10-01T12:00:00"
    con.execute("INSERT INTO courses(semester,id,name,short,code,section,source,excluded,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?, ?,?,?)",
                (SEMESTER, cid, SECRET, SECRET, SECRET, SECRET, "manual", int(excluded), stamp, stamp))
    if meetings is not None:
        version(con, cid, meetings, valid_from)
    store.set_meta(con, "updated_at", stamp)
    con.commit()
    return cid


def version(con, cid, meetings, valid_from=""):
    con.execute("INSERT INTO meetings VALUES(?,?,?,?,?,?)",
                (SEMESTER, cid, valid_from, json.dumps(meetings), "user", "2026-10-01T12:00:00"))
    con.commit()


def meeting(wd=0, periods=(1, 2), room="공7-223"):
    return {"weekday": wd, "periods": list(periods), "room": room}


def block(start, end, room="공7-223", state="scheduled"):
    return {"kind": "class", "start": start, "end": end, "building": room, "state": state}


def test_missing_does_not_create_database_or_parent(tmp_path):
    missing = tmp_path / "not-created" / "attendance.db"
    result = meal_context.export_context("2026-10-19", db_path=missing)
    assert result["status"] == "missing" and result["blocks"] == []
    assert result["semester"] == {"id": None, "start": None, "end": None}
    assert not missing.parent.exists()


def test_registered_courses_are_distinct_from_no_classes_today(db):
    assert meal_context.export_context("2026-10-19")["status"] == "no_timetable"
    seed(db)
    missing = meal_context.export_context("2026-10-19")
    assert missing["status"] == "incomplete"
    assert (missing["courseCount"], missing["missingTimetableCount"]) == (1, 1)
    version(db, "private-course-id", [meeting()])
    free_day = meal_context.export_context("2026-10-20")
    assert free_day["status"] == "ready" and free_day["blocks"] == []


def test_split_morning_afternoon_and_room_changes(db):
    seed(db, [meeting(periods=(1, 2)), meeting(periods=(5,), room="공2-101"),
              meeting(periods=(6,), room="공3-201")])
    result = meal_context.export_context("2026-10-19")
    assert result["status"] == "ready"
    assert result["blocks"] == [block("09:00", "10:50"), block("13:00", "13:50", "공2-101"),
                                 block("14:00", "14:50", "공3-201")]


def test_noncontiguous_stored_periods_never_cover_the_gap(db):
    seed(db, [meeting(periods=(1, 5))])
    assert meal_context.export_context("2026-10-19")["blocks"] == [block("09:00", "09:50"), block("13:00", "13:50")]


def test_effective_version_not_latest_version(db):
    cid = seed(db, [meeting(room="공2-101")])
    version(db, cid, [meeting(wd=1, periods=(5,), room="공7-223")], "2026-11-01")
    assert meal_context.export_context("2026-10-19")["blocks"] == [block("09:00", "10:50", "공2-101")]
    assert meal_context.export_context("2026-11-02")["blocks"] == []
    assert meal_context.export_context("2026-11-03")["blocks"] == [block("15:00", "16:15")]


def test_only_future_version_is_incomplete(db):
    seed(db, [meeting()], valid_from="2026-11-01")
    result = meal_context.export_context("2026-10-19")
    assert result["status"] == "incomplete" and result["missingTimetableCount"] == 1


def test_holiday_and_user_restore_preserve_f3_state(db):
    cid = seed(db, [meeting()])
    assert meal_context.export_context("2026-10-05")["blocks"] == [block("09:00", "10:50", state="canceled")]
    service.patch_session(db, f"cl:{cid}:2026-10-05", {"state": "scheduled"}, now=datetime(2026, 10, 20))
    db.commit()
    assert meal_context.export_context("2026-10-05")["blocks"] == [block("09:00", "10:50")]
    # 원래 휴업일에 수업한 것으로 복구하면 학교 지정 보강도 F3 규칙대로 사라진다.
    assert meal_context.export_context("2026-12-09")["blocks"] == []


def test_notice_cancel_and_manual_restore(db):
    cid = seed(db, [meeting(wd=2)])
    make_eclass([(cid, "2026-10-20 12:00", SECRET, "10월 21일 휴강")])
    assert meal_context.export_context("2026-10-21")["blocks"] == [block("09:00", "10:50", state="canceled")]
    service.patch_session(db, f"cl:{cid}:2026-10-21", {"state": "scheduled"}, now=datetime(2026, 10, 22))
    db.commit()
    assert meal_context.export_context("2026-10-21")["blocks"] == [block("09:00", "10:50")]


def test_school_makeup_uses_original_meeting_but_actual_weekday_time(db):
    rows = [r for r in F1_ROWS if r[0] != "10. 5.(월) 개천절 대체휴일 보강"]
    make_f1([*rows, ("10. 5.(월) 개천절 대체휴일 보강", "etc", "2026-12-08", None)])
    cid = seed(db, [meeting(periods=(5,), room="공2-101")])
    # 보강일에는 다른 시간표를 쓰고 있어도 원래 휴업일에 해당하는 판·요일을 사용한다.
    version(db, cid, [meeting(wd=2, periods=(1,), room="공7-223")], "2026-11-01")
    result = meal_context.export_context("2026-12-08")
    assert result["blocks"] == [block("15:00", "16:15", "공2-101")]


def test_user_makeup_has_no_invented_room(db):
    cid = seed(db, [meeting()])
    service.add_makeup(db, {"courseId": cid, "date": "2026-10-17", "periods": [3, 4], "memo": SECRET},
                       semester=SEMESTER, now=datetime(2026, 10, 20))
    db.commit()
    result = meal_context.export_context("2026-10-17")
    assert result["blocks"] == [block("11:00", "12:50", "")]
    assert result["warnings"] and SECRET not in json.dumps(result)


def test_period_override_and_excluded_courses(db):
    seed(db, [meeting(periods=(1,))])
    seed(db, None, cid="excluded-private-course", excluded=True)
    service.set_period_map(db, {"mwf": {"1": ["08:40", "09:30"]}})
    db.commit()
    result = meal_context.export_context("2026-10-19")
    assert result["blocks"] == [block("08:40", "09:30")]
    assert (result["courseCount"], result["missingTimetableCount"], result["status"]) == (1, 0, "ready")


def test_outside_semester_and_missing_semester(db):
    seed(db, [meeting()])
    outside = meal_context.export_context("2027-01-04", SEMESTER)
    assert outside["status"] == "outside_semester" and outside["blocks"] == []
    make_f1([])
    missing = meal_context.export_context("2026-10-19", SEMESTER)
    assert missing["status"] == "incomplete" and missing["semester"]["start"] is None


def test_read_only_and_private_fields_never_exported(db, monkeypatch):
    cid = seed(db, [meeting()])
    db.execute("INSERT INTO marks VALUES(?,?,?,?,?,?,?,?)",
               (f"cl:{cid}:2026-10-19", SEMESTER, cid, "2026-10-19", "absent", None, SECRET, "2026-10-20T12:00:00"))
    db.execute("INSERT INTO meta VALUES('warning_gpa', '1.75')")
    db.commit()
    before = (hashlib.sha256(C.DB_PATH.read_bytes()).digest(), C.DB_PATH.stat().st_mtime_ns,
              tuple(db.execute("SELECT * FROM meta").fetchall()), tuple(db.execute("SELECT * FROM alerts").fetchall()))
    def forbidden(*args, **kwargs):
        raise AssertionError("writing connector or overview must not run")
    monkeypatch.setattr(store, "connect", forbidden)
    monkeypatch.setattr(service, "overview", forbidden)
    result = meal_context.export_context("2026-10-19")
    assert result["status"] == "ready"
    assert set(result) == {"version", "date", "semester", "blocks", "status", "warnings", "updatedAt", "courseCount", "missingTimetableCount"}
    text = json.dumps(result, ensure_ascii=False)
    for private in (SECRET, cid, "absent", "1.75", "courseId", "memo", "attendance", "academicWarning"):
        assert private not in text
    after = (hashlib.sha256(C.DB_PATH.read_bytes()).digest(), C.DB_PATH.stat().st_mtime_ns,
             tuple(db.execute("SELECT * FROM meta").fetchall()), tuple(db.execute("SELECT * FROM alerts").fetchall()))
    assert before == after


def test_sqlite_uri_escapes_path(db, tmp_path):
    seed(db, [meeting()])
    destination = tmp_path / "한글 # 공백" / "attendance.db"
    destination.parent.mkdir()
    copy = sqlite3.connect(destination)
    db.backup(copy)
    copy.close()
    result = meal_context.export_context("2026-10-19", db_path=destination)
    assert result["status"] == "ready" and result["blocks"]


def test_corrupt_database_errors_are_sanitized(tmp_path):
    corrupt = tmp_path / f"{SECRET}.db"
    corrupt.write_text(SECRET)
    result = meal_context.export_context("2026-10-19", db_path=corrupt)
    assert result["status"] == "unavailable"
    assert SECRET not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert corrupt.read_text() == SECRET


@pytest.mark.parametrize("day,semester", [(SECRET, None), ("2026-02-30", None), ("2026-10-19", SECRET)])
def test_invalid_selection_does_not_echo_input(day, semester):
    result = meal_context.export_context(day, semester)
    assert result["status"] == "unavailable" and SECRET not in json.dumps(result)


def test_cli_only_emits_contract_json(db, capsys):
    seed(db, [meeting()])
    assert main(["campus-context", "--date", "2026-10-19", "--semester", SEMESTER]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["status"] == "ready" and result["date"] == "2026-10-19"
    assert output.err == "" and SECRET not in output.out
