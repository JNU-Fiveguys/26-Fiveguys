"""F3 명령줄 — `desktop/cli.py attendance` 로 부른다 (개발 venv · 표준 라이브러리만).

    cli.py attendance status [--semester 2026-2]      과목별 총 횟수 · 유효 결석 / 허용 · 남은 여유 · 상태 · 근거
    cli.py attendance sessions 운영체제                회차 목록 (과목 이름 일부 또는 id)
    cli.py attendance semester                        학기 범위 · 휴업일 · 학교 지정 보강일 (학사일정 F1 에서)
    cli.py attendance import                          시간표 자동으로 가져오기 (학사정보시스템 공개 조회, 로그인 불필요)
    cli.py attendance mark <회차 id> absent            출결 적기 (present·absent·late·excused·none) · canceled/scheduled = 휴강/되돌리기
    cli.py attendance notices                         e클래스 공지에서 찾은 휴강 (자동 휴강 원천)
    cli.py attendance parse 월5월6수5                  강의시간 원문 해석 (시각 포함)
    cli.py attendance periods                         교시 ↔ 시각 (학교 시간표 모듈)
    cli.py attendance campus-context --date YYYY-MM-DD  식사 추천용 하루 시간표(JSON, 읽기 전용)
    cli.py attendance clear                           출결 기록·시간표·설정 전부 지우기
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Optional

from . import academic_calendar as AC
from . import config as C
from . import jobs, notices, service, sessions, store, timetable

_COURSE = re.compile(r"^(?P<short>.*?)\s*\[(?P<section>\d+)\]\s*\((?P<code>[A-Za-z0-9]+)\)\s*$")


def eclass_courses() -> Optional[list[dict]]:
    """F6_Eclass_agent/data/courses.json → 과목 목록 (대시보드에서는 백엔드가 같은 모양을 넘겨준다)."""
    try:
        raw = json.loads(C.ECLASS_COURSES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = []
    for i, c in enumerate(raw):
        m = _COURSE.match(c.get("name", ""))
        out.append({"id": str(c.get("id", i)), "name": c.get("name", ""),
                    "short": m.group("short").strip() if m else c.get("name", ""),
                    "code": m.group("code") if m else "", "section": m.group("section") if m else ""})
    return out


def _profile() -> Optional[dict]:
    c2 = C.PROJECT_ROOT / "C2_Profile_agent"
    if str(c2) not in sys.path:
        sys.path.insert(0, str(c2))
    try:
        from student import api as c2api      # type: ignore[import-not-found]
        return c2api.profile_for_matching()
    except Exception:                         # noqa: BLE001
        return None


def _n(x) -> str:
    return "—" if x is None else f"{x:g}"


def cmd_status(a) -> int:
    with store.connect() as con:
        o = service.overview(con, a.semester, profile=_profile(), eclass=eclass_courses())
    sem = o["semester"]
    print(f"{sem['label']} · 개강 {sem['start'] or '?'} ~ 종강 {sem['end'] or '?'}  (학교 공식 출결 기록이 아닙니다 — 내가 입력한 값 기준)")
    for w in sem["warnings"]:
        print(f"  ⚠ {w}")
    for c in o["courses"]:
        s = c["summary"]
        tag = " [제외]" if c["excluded"] else ""
        if not c["timetable"]["meetings"]:
            print(f"- {c['short']}{tag}: 시간표 없음 — `cli.py attendance import` 또는 화면에서 요일·교시 입력")
            continue
        print(f"- {c['short']}{tag} ({c['timetable']['text']}, {'자동' if c['timetable']['filledBy'] == 'auto' else '내가 고침'})")
        print(f"    {s['levelLabel']:<3} 결석 {s['effectiveAbsent']}회 / 허용 {_n(s['allowed'])}회 · 더 빠질 수 있는 횟수 "
              f"{s['spareSessions']}회 · 미입력 {s['uncheckedSessions']}회")
        print(f"    {s['basis']}")
        for h in c["noticeHints"]:
            print(f"    ⚠ 공지 '{h['reason']}' — {h['why']}")
    w = o["academicWarning"]
    if w.get("message"):
        print(f"학사경고: {w['message']}")
    return 0


def cmd_sessions(a) -> int:
    with store.connect() as con:
        _, courses = service.build_semester(con, a.semester or service.current_semester(con, AC.load(), datetime.now().date()),
                                            datetime.now())
    hit = [c for c in courses if a.course in (c["id"], c["short"]) or a.course in c["short"]]
    if not hit:
        print("그런 과목이 없습니다")
        return 1
    for c in hit:
        print(f"{c['short']} — {c['summary']['basis']}")
        for s in c["sessions"]:
            why = {"academic": "학사일정", "eclass": "e클래스 공지", "user": "내가 표시"}.get(s["cancelSource"] or "", "")
            st = f"휴강({why}{' · ' + s['autoCancel']['reason'] if s['autoCancel'] and s['state'] == 'canceled' else ''})"                 if s["state"] == "canceled" else ""
            mk = " 보강" + (f"({s['makeupName']})" if s["makeupName"] else "") if s["kind"] == "makeup" else ""
            att = service.ATTENDANCE_LABEL[s["attendance"]] if s["state"] == "scheduled" else ""
            print(f"  {s['date']} {C.WEEKDAYS[s['weekday']]} {'·'.join(map(str, s['periods']))}교시 "
                  f"{s['start']}~{s['end']}{mk} {st}{att}   {s['id']}")
    return 0


def cmd_semester(a) -> int:
    cal = AC.load()
    with store.connect() as con:
        sid = a.semester or service.current_semester(con, cal, datetime.now().date())
        sem = service.resolve_semester(con, cal, sid)
    print(json.dumps(sem, ensure_ascii=False, indent=2))
    return 0


def cmd_import(a) -> int:
    ec = eclass_courses()
    with store.connect() as con:
        sid = a.semester or service.current_semester(con, AC.load(), datetime.now().date())
        if ec:
            service.sync_courses(con, sid, ec)
        targets = service.import_targets(con, sid)
    st = jobs.start_import(sid, targets, (_profile() or {}).get("grade"), background=False)
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0 if st.get("ok") else 1


def cmd_mark(a) -> int:
    body = {"state": a.value} if a.value in ("canceled", "scheduled") else {"attendance": None if a.value == "none" else a.value}
    with store.connect() as con:
        res = service.patch_session(con, a.id, body)
    s = res["course"]["summary"]
    print(f"{res['course']['short']}: {s['levelLabel']} — 결석 {s['effectiveAbsent']}회 / 허용 {_n(s['allowed'])}회")
    for al in res["alerts"]:
        print(f"  알림: {al['title']} — {al['body']}")
    return 0


def cmd_parse(a) -> int:
    ms = timetable.parse_times(a.raw)
    pm = sessions.default_period_map()
    for m in ms:
        s, e = sessions.period_span(m["weekday"], m["periods"], pm)
        print(f"{timetable.meeting_text(m)}  {s}~{e}")
    return 0


def cmd_notices(a) -> int:
    n = notices.load()
    if not n["available"]:
        print(f"e클래스 게시판 목록이 없습니다: {C.ECLASS_MANIFEST}")
        return 1
    names = {c["id"]: c["short"] for c in eclass_courses() or []}
    for cid, v in n["byCourse"].items():
        for d, src in sorted(v["cancels"].items()):
            print(f"{names.get(cid, cid)}  {d} 휴강  ← '{src['reason']}' ({src['posted']})")
        for h in v["hints"]:
            print(f"{names.get(cid, cid)}  ? '{h['reason']}' — {h['why']}")
    return 0


def cmd_periods(a) -> int:
    with store.connect() as con:
        print(json.dumps(service.period_view(con), ensure_ascii=False, indent=2))
    return 0


def cmd_clear(a) -> int:
    for p in C.DB_PATH.parent.glob("attendance.db*"):
        p.unlink()
    print("출결 기록·시간표·설정을 지웠습니다")
    return 0


def cmd_campus_context(a) -> int:
    from .meal_context import export_context
    # 모든 상태는 같은 JSON 계약으로 반환한다. 예외 메시지·과목명 등은 출력하지 않는다.
    print(json.dumps(export_context(a.date, a.semester), ensure_ascii=False))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="attendance", description="F3 출결·학사경고 예방")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("semester", cmd_semester), ("import", cmd_import)):
        p = sub.add_parser(name)
        p.add_argument("--semester")
        p.set_defaults(fn=fn)
    p = sub.add_parser("sessions")
    p.add_argument("course")
    p.add_argument("--semester")
    p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("mark")
    p.add_argument("id")
    p.add_argument("value", choices=[*service.ATTENDANCE, "none", "canceled", "scheduled"])
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("parse")
    p.add_argument("raw")
    p.set_defaults(fn=cmd_parse)
    sub.add_parser("periods").set_defaults(fn=cmd_periods)
    sub.add_parser("notices").set_defaults(fn=cmd_notices)
    p = sub.add_parser("campus-context", help="식사 추천용 하루 시간표를 읽기 전용으로 내보내기")
    p.add_argument("--date", required=True)
    p.add_argument("--semester")
    p.set_defaults(fn=cmd_campus_context)
    sub.add_parser("clear").set_defaults(fn=cmd_clear)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (service.Invalid, service.NotFound, service.Conflict, timetable.ParseError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
