"""유니버스(Univ-Us) 데스크톱 앱의 로컬 서버 — FastAPI. backend/desktop.py(사이드카 진입점)가 띄운다.

- /api/*            프론트가 쓰는 JSON API (앱 창의 세션 쿠키가 있어야 한다 — desktop_session)
- /                 frontend/out (next build 결과) 가 있으면 정적으로 서빙. 없으면 안내 페이지.
개발 모드(desktop/ 에서 npm run dev)에서는 `next dev`(3000) 가 /api·/desktop/launch 를 여기(8020)로 넘겨준다 (frontend/next.config.ts rewrites).

이 파일은 **붙이는 곳**이다. 기능 코드는 전부 기능 폴더에 있다 (C1 캘린더 · F6 · C2 · F1 · F2 · F3 · F4 · F5 · F7 · F8 · F11 · F12 · C4 브리핑).
"""
from __future__ import annotations

import hmac
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import academic, activity, attendance, briefing, calendar_events, exams, graduation, materials, placement, priority, scholarship
from . import config as C
from . import eclass_data, login_creds, student_profile


@asynccontextmanager
async def lifespan(_: FastAPI):
    calendar_events.init()               # C1: 표 만들기 · 옛 자리(univ_us_local/data)의 일정 DB 옮기기
    student_profile.migrate_legacy()     # 예전 kv 프로필이 남아 있으면 C2 로 옮긴다 (한 번)
    yield


app = FastAPI(title="Univ-Us Local", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)

# Host 헤더가 localhost/127.0.0.1 이 아니면 거절 (DNS 리바인딩 대비)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=C.ALLOWED_HOSTS)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """정적 프론트 캐시 규칙. 헤더가 없으면 브라우저가 옛 index.html 을 추정 캐시로 계속 써서
    `npm run build` 뒤에도 옛 화면이 보인다 → HTML 은 매번 재검증(no-cache), 해시 붙은 _next/static 은 영구 캐시."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api"):
        if path.startswith("/_next/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.middleware("http")
async def desktop_session(request: Request, call_next):
    """데스크톱 앱 창에서 온 요청만 받는다 — desktop.py 가 띄웠을 때(C.SESSION_TOKEN)만 켜진다.
    이 서버는 성적·일정·학교 로그인 세션을 다루므로, 같은 PC 의 다른 프로그램·브라우저 탭이 127.0.0.1 로 부르지 못하게
    앱 창만 가진 HttpOnly 쿠키를 요구한다. 쿠키는 /desktop/launch 의 한 번 쓰는 코드로만 받을 수 있다."""
    if not C.SESSION_TOKEN or request.url.path == "/desktop/launch":
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(C.SESSION_COOKIE, ""), C.SESSION_TOKEN):
        return await call_next(request)
    return JSONResponse({"detail": "유니버스 앱 창에서만 열 수 있습니다"}, status_code=403)


@app.middleware("http")
async def check_origin(request: Request, call_next):
    """브라우저가 보내는 변경 요청은 허용된 Origin 에서만. Origin 이 없는 요청(curl 등)은 로컬 도구로 본다."""
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in C.ALLOWED_ORIGINS:
            return JSONResponse({"detail": f"허용되지 않은 Origin: {origin}"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- API

@app.get("/api/courses")
def get_courses() -> list[dict]:
    return eclass_data.load_courses()


@app.get("/api/status")
def get_status() -> dict:
    eclass_data.deliver()                # F6: 때가 된 마감 알림·마감 변경·수집 실패를 알림 센터로 (1분마다 불린다)
    eclass = eclass_data.status_block()
    calendar = calendar_events.summary()
    counts = calendar["counts"]
    return {
        "updated_at": eclass.get("lastOkAt") or eclass.get("reconciledAt"),   # 마지막으로 e클래스 수집에 성공한 시각
        "sync": eclass_data.sync_state(),
        "counts": {
            "courses": len(eclass_data.load_courses()),
            "deadlines": (eclass.get("counts") or {}).get("total", 0),
            "userEvents": counts["total"],
            "todos": counts["todos"],
            "todosDone": counts["done"],
        },
        "categories": calendar["categories"],
        "eclassDataDir": eclass_data.data_dir(),
        "log": eclass_data.last_log_lines(),
        "calendar": calendar,                # C1: 내 일정 DB 자리 · 못 불러왔으면 error
        "eclass": eclass,                    # F6: 진행 중·지난 마감 수 · 연속 실패 · 로그인 필요 · 재시도 · 다음 주기 · updatedAt
        "academic": academic.status(),       # F1: 원천별 수집 상태 · 확인 필요 건수 · updatedAt
        "graduation": graduation.status(),   # F2: 남은 학점 · 판정 · 한 줄 요약 · updatedAt (기능 타일)
        "attendance": attendance.status(),   # F3: 위험 과목 · 확인 안 한 수업 · 시간표 미입력 · updatedAt (기능 타일)
        "materials": materials.status(),     # F4: 강의자료 수 · 쪽수 · 확인 필요 · updatedAt (기능 타일)
        "exams": exams.status(),             # F5: 오늘 분량 · 다가오는 시험 · 확인 필요 · 밀린 계획 · updatedAt (기능 타일)
        "priority": priority.status(),       # F7: 지금 해야 함 · 합계 · 놓친 마감 · 상위 3건 (대시보드 '먼저 할 것')
        "placement": placement.status(),     # F8: 앞으로 남은 학습 블록 · 오늘 블록 · updatedAt (바뀌면 화면이 캘린더를 다시 받는다)
        "scholarship": scholarship.status(),  # F11: 해당 · 확인 필요 · 마감 임박 · 수집 상태 (겸사겸사 새 장학 알림 배달 · 06·18시 자동 수집)
        "activity": activity.status(),        # F12: 내 관심사 · 관련 있음 · 담은 것 · 마감 임박 (겸사겸사 관심 기회 알림 · 장학 다음 06·18시 수집)
        # C4: 오늘 브리핑 상태 · 건수 · updatedAt (겸사겸사 예정 시각이 지났으면 만든다 — 위 기능들이 알림을 배달한 뒤라 맨 끝에 둔다)
        "briefing": briefing.status(),
    }


@app.post("/api/sync")
def post_sync() -> dict:
    """e클래스 수집(F6)을 백그라운드로 띄운다 — 이미 돌고 있으면 새로 띄우지 않는다. 진행 상황은 /api/status 의 sync 로 본다."""
    return eclass_data.start_sync()


def _profile_changed() -> None:
    """프로필이 바뀌면 F1 '내 해당'·F2 졸업요건 기준·F3 학사경고 안내·F11 장학 판정·F12 관심사 등급이 달라진다 — updatedAt 을 올려 화면이 다시 받게 한다 (C2-R08)."""
    academic.touch()
    graduation.touch()
    attendance.touch()
    scholarship.touch()                  # 민감정보(소득구간·지역·해당 사항)를 넣으면 관련 장학을 모두 다시 판정 (F11-R32)
    activity.touch()                     # 관심사(태그·키워드·제외어)를 바꾸면 대외활동 공지를 모두 다시 매긴다 (F12-R15)


# ---------------------------------------------------------------- 기능 라우터

# C1 캘린더 — /api/events (C1_Calendar_agent/calendar_core/api.py)
# 내 일정·할 일은 C1 이 저장하고, 다른 기능이 캘린더에 얹는 일정은 아래 소스 함수로 넘겨준다 (C1 3절).
# F8 이 공강을 계산할 때 '차지된 시간'으로 읽는 일정 — 수업(F3) · 시험(F5) + 내 일정·할 일(C1, collect 가 붙인다).
# 학사 일정(F1)은 넣지 않는다 — 학교 전체 일정이라 내 시간이 아니다. '내 일정에 넣기'로 만든 것은 C1 내 일정이라 들어간다(2026-10-07).
# 과제 마감은 한 시점이라 시간을 차지하지 않고, 학습 블록은 F8 자신이라 넣지 않는다(돌고 돌지 않게).
_PLACEMENT_BUSY = (attendance.calendar_events, exams.calendar_events)


def _busy_events(start, end):
    return calendar_events.collect(_PLACEMENT_BUSY, start, end)


CALENDAR_SOURCES = (
    lambda start, end: eclass_data.load_deadline_events(),   # F6 과제·퀴즈·동영상 마감 (kind=deadline)
    academic.calendar_events,                                # F1 학사 일정 (kind=academic, 내 캘린더에 등록된 것만)
    attendance.calendar_events,                              # F3 수업 회차 (kind=class)
    exams.calendar_events,                                   # F5 시험(kind=exam) — 공부 계획은 공부 캘린더에만
    placement.calendar_source(_busy_events),                 # F8 과제·할 일 블록(kind=study) — 공부 블록은 공부 캘린더에만
)
if (_c1_router := calendar_events.router(CALENDAR_SOURCES)) is not None:
    app.include_router(_c1_router)

# F6 과제·마감 — /api/assignments* · /api/sources/eclass · /api/sync/login (F6_Eclass_agent/eclass/api.py)
# F1 의 /api/sources/{key} 보다 먼저 붙여야 /api/sources/eclass 가 F6 로 간다.
if (_f6_router := eclass_data.router()) is not None:
    app.include_router(_f6_router)

# C3 자동 로그인 정보 — /api/login/creds (C3_Login_agent/login/api.py)
if (_c3_router := login_creds.router()) is not None:
    app.include_router(_c3_router)

# C2 프로필·학과 마스터 — /api/profile* · /api/master/* (C2_Profile_agent/student/api.py)
if (_c2_router := student_profile.router(on_change=_profile_changed)) is not None:
    app.include_router(_c2_router)

# F1 학사 일정 — /api/academic/* · /api/sources* · /api/notifications* (F1_Bachelor_agent/bachelor/api.py)
if (_f1_router := academic.router()) is not None:
    app.include_router(_f1_router)

# F2 졸업요건 — /api/graduation/* (F2_Graduation_agent/graduation/api.py)
# 이수 내역을 가져올 때 같이 읽은 평점·학년은 C2 프로필에 '자동'으로 넣고, 그러면 F1 도 다시 판정하게 한다.
if (_f2_router := graduation.router(on_profile_change=academic.touch)) is not None:
    app.include_router(_f2_router)

# F3 출결 — /api/attendance/* (F3_Attendance_agent/attendance/api.py)
# 수업 회차는 /api/events 에 kind=class 로 섞이고, 상태가 올라가면 경고가 알림 센터(F1 notifications)에 들어간다.
if (_f3_router := attendance.router()) is not None:
    app.include_router(_f3_router)

# F4 강의자료 — /api/materials* (F4_Textbook_agent/textbook/api.py)
# F6 가 내려받아 둔 파일을 과목별 자료로 세우고, 원문을 이 서버가 스트림으로 넘겨준다 (밖으로는 나가지 않는다).
if (_f4_router := materials.router()) is not None:
    app.include_router(_f4_router)

# F5 시험 공부 일정 — /api/exams* · /api/study-plans* (F5_Test_agent/exams/api.py)
# e클래스 공지에서 시험을 찾아 '확인 필요'로 세우고, 확인한 계획의 날짜별 분량은 저녁 시간대(19~24시)에 공부 캘린더로 놓인다.
# 공부 캘린더에는 F8 이 낮 공강(09~18시)에 넣은 공강 공부 블록도 섞는다(2026-10-07) — 그 블록은 전체 캘린더에는 없다.
if (_f5_router := exams.router(extra_blocks=placement.study_source(_busy_events))) is not None:
    app.include_router(_f5_router)

# F7 과제 우선순위 — /api/priority* · /api/settings/priority (F7_Task_agent/tasks/api.py)
# F6 과제 원장을 마감 + 예상 소요시간으로 '지금 해야 함 / 이번 주 / 나중에'로 가른다. 순위는 저장하지 않고 부를 때마다 계산한다.
# 오늘 남은 시간에서 뺄 일정은 수업(F3)·시험(F5)·내 일정(C1)뿐이라 그 소스만 합친다(과제 마감·학사 일정은 시간을 차지하지 않는다).
_BUSY_SOURCES = (attendance.calendar_events, exams.calendar_events)
if (_f7_router := priority.router(lambda start, end: calendar_events.collect(_BUSY_SOURCES, start, end))) is not None:
    app.include_router(_f7_router)

# F8 공강 학습 플랜 — /api/placement* · /api/settings/availability (F8_Plan_agent/placement/api.py)
# 낮 공강(09~18시)에 F7 순서대로의 과제와 할 일을 넣고, 남는 공강은 공부 블록(남은 진도율 ÷ 시험까지 남은 날수)으로 채운다.
# 저녁(19~24시)은 F5 시험 공부 계획 몫. 미리보기는 저장하지 않고, '배치하기'를 눌렀을 때만 블록(kind=study)이 생긴다.
if (_f8_router := placement.router(_busy_events)) is not None:
    app.include_router(_f8_router)


# F11 장학 공지 매칭·신청서 초안 — /api/scholarships* (F11_Scholarship_agent/scholarship/api.py)
# 학교 장학안내 · 부서 · 사업단 · 내 단과대학·학과 게시판과 상시 장학 안내를 모아 C2 프로필(민감정보 포함)과 조건별로 대조한다.
# 신청 마감은 '캘린더에 넣기'로 C1 내 일정이, 제출 서류는 할 일이 된다. 제출은 하지 않는다 — 초안·체크리스트·링크까지.
if (_f11_router := scholarship.router()) is not None:
    app.include_router(_f11_router)

# F12 대외활동(맞춤 기회) — /api/opportunities* (F12_Activity_agent/activity/api.py)
# F11 과 같은 원천(게시판 수집기·원천 목록을 빌려 쓴다)에서 장학이 아닌 공지 — 교내 프로그램·공모전·대외활동·취업·해외·창업·기자재 대여 … —
# 를 모아 C2 프로필의 관심사로 '내 관심사 / 관련 있음 / 관심 밖'을 가른다. 담으면 신청 마감이 C1 내 일정이 되고, 신청서 초안까지 만든다(제출은 하지 않는다).
if (_f12_router := activity.router()) is not None:
    app.include_router(_f12_router)

# C4 브리핑 (요구사항 F10 아침 브리핑을 공통 기능으로) — /api/briefing* · /api/settings/briefing (C4_Briefing_agent/briefing/api.py)
# 각 기능이 브리핑용으로 내놓은 값(F3 오늘 수업 · F6 마감 · F7 먼저 할 것 · F1 학사 D-3 · F5 오늘 공부 · F8 블록 · F11·F12 마감
# · 알림 센터의 안 읽은 알림)을 모아 하루 한 번 스냅샷을 만든다. 오늘 일정·마감은 /api/events 와 같은 합치기에서 읽는다.
if (_c4_router := briefing.router(lambda start, end: calendar_events.collect(CALENDAR_SOURCES, start, end))) is not None:
    app.include_router(_c4_router)


# ---------------------------------------------------------------- 데스크톱 앱

@app.get("/desktop/launch", include_in_schema=False)
def desktop_launch(code: str = ""):
    """앱 창이 맨 처음 여는 주소 — 한 번 쓰는 코드를 세션 쿠키로 바꾸고 첫 화면으로 보낸다."""
    if not C.SESSION_TOKEN or not C.consume_launch_code(code):
        return JSONResponse({"detail": "이미 쓰였거나 맞지 않는 실행 코드입니다 — 앱을 다시 실행하세요"}, status_code=403)
    resp = RedirectResponse("/", status_code=303)
    # 맥(WKWebView)은 tauri:// 시작 화면에서 넘어온 이 이동을 다른 사이트 이동으로 봐서, 303 에 실은 Strict 쿠키를
    # 리다이렉트된 '/' 요청에 붙이지 않는다 → 첫 화면이 403 (2026-10-27 실측). 앱 창은 바깥 주소를 열지 않고(on_navigation)
    # 변경 요청은 Origin 검사가 막으므로 맥만 Lax 로 둔다. Windows(WebView2)는 그대로 Strict.
    samesite = "lax" if sys.platform == "darwin" else "strict"
    resp.set_cookie(C.SESSION_COOKIE, C.SESSION_TOKEN, httponly=True, samesite=samesite, path="/")
    print("desktop: 앱 창이 연결됨 (실행 코드 사용)", file=sys.stderr, flush=True)
    return resp


# ---------------------------------------------------------------- 정적 프론트

if C.FRONTEND_OUT.exists():
    app.mount("/", StaticFiles(directory=str(C.FRONTEND_OUT), html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    def index_placeholder() -> str:
        return (
            "<h1>Univ-Us Local</h1>"
            "<p>프론트 빌드(<code>frontend/out</code>)가 없습니다. 개발 중이면 "
            "<a href='http://localhost:3000'>http://localhost:3000</a> (next dev) 로 여세요.<br>"
            "배포용은 <code>frontend</code> 에서 <code>npm run build</code> 를 실행하면 여기서 바로 서빙됩니다.</p>"
            "<p>API 문서: <a href='/api/docs'>/api/docs</a></p>"
        )
