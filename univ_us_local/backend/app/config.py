"""univ_us_local 백엔드 설정 — 개인 로컬 서버(127.0.0.1)용.

원칙
  - 127.0.0.1 에만 바인딩한다. LAN 에 열려면 인증·HTTPS 를 먼저 붙인다.
  - 기능마다 폴더가 따로 있다(C1 · F6_Eclass_agent · F1 · C2 · F2 · F3 · F4 · F5 · F7 · F8 · F11 · F12 · C4). 이 서버는 그 API 를 붙이기만 한다.
    학교 로그인·브라우저는 C3_Login_agent 몫이다 — 이 서버의 .venv 에는 playwright 가 없다.
  - 내 일정·할 일도 C1_Calendar_agent 로 나갔다 (그 폴더의 data/univus.db). 여기에는 DB 가 없다.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent          # univ_us_local/
PROJECT_ROOT = ROOT.parent                                    # 26 WE-Meet Project/

# 기능 폴더 — 코드·DB 는 그 폴더에 있고, 여기서는 그 API 를 붙이기만 한다.
C1_AGENT_DIR = Path(os.environ.get("C1_AGENT_DIR") or PROJECT_ROOT / "C1_Calendar_agent")   # C1 서비스 캘린더 (calendar_core.api)
F6_AGENT_DIR = Path(os.environ.get("F6_AGENT_DIR") or PROJECT_ROOT / "F6_Eclass_agent")      # F6 e클래스 과제·마감 (eclass.api)
C3_AGENT_DIR = Path(os.environ.get("C3_AGENT_DIR") or PROJECT_ROOT / "C3_Login_agent")       # C3 포털 자동 로그인 (F6·C2·F2 가 빌려 쓴다)
F1_AGENT_DIR = Path(os.environ.get("F1_AGENT_DIR") or PROJECT_ROOT / "F1_Bachelor_agent")   # F1 학사 일정 (bachelor.api)
C2_AGENT_DIR = Path(os.environ.get("C2_AGENT_DIR") or PROJECT_ROOT / "C2_Profile_agent")    # C2 프로필·학과 마스터 (student.api)
F2_AGENT_DIR = Path(os.environ.get("F2_AGENT_DIR") or PROJECT_ROOT / "F2_Graduation_agent")  # F2 졸업요건 (graduation.api)
F3_AGENT_DIR = Path(os.environ.get("F3_AGENT_DIR") or PROJECT_ROOT / "F3_Attendance_agent")  # F3 출결 (attendance.api)
F4_AGENT_DIR = Path(os.environ.get("F4_AGENT_DIR") or PROJECT_ROOT / "F4_Textbook_agent")   # F4 강의자료 (textbook.api)
F5_AGENT_DIR = Path(os.environ.get("F5_AGENT_DIR") or PROJECT_ROOT / "F5_Test_agent")       # F5 시험 공부 일정 (exams.api)
F7_AGENT_DIR = Path(os.environ.get("F7_AGENT_DIR") or PROJECT_ROOT / "F7_Task_agent")       # F7 과제 우선순위 (tasks.api)
F8_AGENT_DIR = Path(os.environ.get("F8_AGENT_DIR") or PROJECT_ROOT / "F8_Plan_agent")       # F8 공강 학습 플랜 (placement.api)
F11_AGENT_DIR = Path(os.environ.get("F11_AGENT_DIR") or PROJECT_ROOT / "F11_Scholarship_agent")  # F11 장학 매칭 (scholarship.api)
F12_AGENT_DIR = Path(os.environ.get("F12_AGENT_DIR") or PROJECT_ROOT / "F12_Activity_agent")     # F12 대외활동 (activity.api)
C4_AGENT_DIR = Path(os.environ.get("C4_AGENT_DIR") or PROJECT_ROOT / "C4_Briefing_agent")        # C4 브리핑 (briefing.api) — 다른 기능을 모은다
CA2_AGENT_DIR = Path(os.environ.get("CA2_AGENT_DIR") or PROJECT_ROOT / "campus" / "CA2_Assistant_agent")  # CA2 캠퍼스 생활 Q&A·대화 (assistant.api)

FRONTEND_OUT = ROOT / "frontend" / "out"                     # `npm run build` 결과 (정적 export)

HOST = os.environ.get("UNIVUS_HOST", "127.0.0.1")
PORT = int(os.environ.get("UNIVUS_PORT", "8020"))                 # desktop.py 가 정한다 (설치본 빈 포트 · 개발 모드 8020)

# 브라우저에서 오는 변경 요청(POST/PATCH/DELETE)은 이 Origin 에서만 받는다 (DNS 리바인딩·CSRF 대비).
ALLOWED_ORIGINS = {
    f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}",
    "http://localhost:3000", "http://127.0.0.1:3000",          # next dev
}
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# ── 데스크톱 앱 세션 (univ_us_local/backend/desktop.py 가 띄울 때만) ──
# 실행마다 새로 만든 값. desktop.py 없이 띄우면(테스트) 비어 있어 아무 검사도 하지 않는다.
# 환경변수에서 pop 하는 것은 이 서버가 띄우는 자식 프로세스(수집기)에 물려주지 않으려는 것이다.
SESSION_TOKEN = os.environ.pop("UNIVUS_SESSION_TOKEN", "") or None
SESSION_COOKIE = "univus_session"
_launch_code = os.environ.pop("UNIVUS_LAUNCH_CODE", "") or None


def consume_launch_code(code: str) -> bool:
    """앱 창이 처음 여는 /desktop/launch?code=… 의 코드 — 한 번만 맞는다 (주소가 새어도 다시 쓸 수 없다)."""
    global _launch_code
    ok = bool(_launch_code) and hmac.compare_digest(code or "", _launch_code)
    if ok:
        _launch_code = None
    return ok

# 과목 색 — courses.json 순서대로 배정 (7과목 + 여유)
COURSE_PALETTE = [  # 흙빛 팔레트 (디자인 v3 "Paper & Pine" — DESIGN.md)
    "#3d6b8c",  # slate blue
    "#a0522d",  # sienna
    "#7a4f8a",  # plum
    "#2f7a73",  # pine teal
    "#9a6a14",  # ochre
    "#a63d52",  # berry
    "#4c5fa6",  # dusk indigo
    "#8a6a4c",  # walnut
    "#55753a",  # moss
    "#b05a7a",  # rose
]
