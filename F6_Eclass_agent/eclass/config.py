"""F6 과제 마감 자동 등록 (e클래스 크롤링) — 설정.

요구사항: 요구사항정의서.md F6 절 / 화면: Frontend-Route.md 11절.

두 가지 python 으로 돈다
  - 수집(collect.py · runner.py)  → C3_Login_agent/.venv (playwright · bs4 · Chromium). 로그인도 C3 의 것을 빌린다.
  - 나머지(store·reconcile·service·notify·api) → **표준 라이브러리 + fastapi** 만. 대시보드 백엔드가 그대로 import 한다.

학교가 공지한 금지선(e클래스 공지 「저작권 유의사항 안내」):
  - 다운로드한 자료를 타인에게 배포·전송         → data/ 폴더는 절대 공유 금지 (F6-R05)
  - 계정정보 공유                                → 로그인 상태는 C3_Login_agent/state 에만
  - 무단복사 방지 조치 무력화                    → 동영상(mod/vod)은 건드리지 않음
  - 수업자료를 인터넷에 게시                     → 깃허브 등 업로드 금지
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # F6_Eclass_agent/
PROJECT_ROOT = ROOT.parent

# C0 OS 공통 계층 (osenv) — venv python 자리·프로세스 확인이 OS 마다 다르다
C0_AGENT_DIR = Path(os.environ.get("C0_AGENT_DIR") or PROJECT_ROOT / "C0_Platform_agent")
if str(C0_AGENT_DIR) not in sys.path:
    sys.path.append(str(C0_AGENT_DIR))
from osenv import FROZEN, browsers_dir, module_cmd, pid_alive, task_command  # noqa: E402,F401 — pid_alive 는 runs

# data/ 아래 경로는 manifest·assignments 에 'data\\<과목>\\…' 처럼 ROOT 기준 상대경로로 적힌다 (F3 공지 휴강이 같은 규칙으로 읽는다).
DATA_DIR = Path(os.environ.get("F6_DATA_DIR") or ROOT / "data")
STATE_DIR = Path(os.environ.get("F6_STATE_DIR") or ROOT / "state")
MANIFEST_FILE = DATA_DIR / "manifest.json"          # 내려받은 파일·글 목록 (재실행 시 건너뛰기, F6-R06)
COURSES_FILE = DATA_DIR / "courses.json"            # 과목·활동 목록 (F3 과목 목록이 이것을 쓴다)
ASSIGNMENTS_FILE = DATA_DIR / "assignments.json"    # 과제 전체 (설명·첨부·제출/채점 상태)
DEADLINES_FILE = DATA_DIR / "deadlines.json"        # 마감 일정 (캘린더 '다가오는 일정' + 과제 + 퀴즈)
DB_PATH = DATA_DIR / "eclass.db"                    # 과제 원장 — 신규·변경·삭제 이력, '내가 체크함', 소요시간 (5절 · 6절)

LOG_FILE = STATE_DIR / "sync.log"                   # 실행 로그 (대시보드 버튼 · 예약 실행)
LOCK_FILE = STATE_DIR / "sync.lock"                 # 도는 동안 잡는 잠금 {pid, started_at, source, attempt} — 중복 실행 방지 (F6-R14)
LAST_RUN_FILE = STATE_DIR / "sync.last.json"        # 마지막 실행 결과 (예전 모양 그대로 + source·attempt·counts·error)
RUNS_FILE = STATE_DIR / "runs.jsonl"                # 실행 이력 한 줄씩 (F6-R16) — 최근 RUNS_KEEP 줄만 남긴다
RETRY_FILE = STATE_DIR / "retry.json"               # 재시도 대기 중이면 {attempt, next_at, source} (F6-R12)
SETTINGS_FILE = STATE_DIR / "settings.json"         # 주기(F6-R18) — 작업 스케줄러 등록 스크립트도 이 값을 읽는다
RUNS_KEEP = 300

# ── C3 포털 자동 로그인 (세션 · 재인증 · 브라우저 · python) ──
C3_AGENT_DIR = Path(os.environ.get("C3_AGENT_DIR") or PROJECT_ROOT / "C3_Login_agent")
C3_BROWSERS = browsers_dir()                     # Chromium (앱 데이터 폴더 / 개발 모드 venv) — 가져오기는 osenv.module_cmd 로 띄운다

# ── e클래스 (sel.jnu.ac.kr, Moodle/유비온) ──
BASE_URL = "https://sel.jnu.ac.kr"
# 과목 목록 후보 페이지 (앞에서부터 시도, 과목이 잡히는 첫 페이지 사용)
COURSE_LIST_URLS = [
    f"{BASE_URL}/local/ubion/user/index.php",   # 유비온 "나의 강의실"
    f"{BASE_URL}/my/",                          # Moodle 기본 대시보드
]
# 마감 일정: 캘린더 '다가오는 일정' + 과제 페이지 + 퀴즈 페이지(마감 일시만)를 합친다 (동영상 시청 기한 포함).
CALENDAR_URL = f"{BASE_URL}/calendar/view.php?view=upcoming"

# 요청 간격(초). 서버 부하 방지 목적이므로 줄이지 말 것. 동시성은 항상 1. (F6-R04)
REQUEST_INTERVAL = 1.5

# 내려받을 확장자 — 문서·코드만 (미디어 없음)
ALLOWED_EXT = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".hwp", ".hwpx",
    ".xlsx", ".xls", ".csv", ".txt", ".md", ".zip",
    ".c", ".cpp", ".h", ".py", ".java", ".ipynb",
}
MAX_FILE_MB = 100               # 파일 하나당 최대 용량 — 대용량 미디어 차단
ATTACHMENT_FAILURE_LIMIT = 3    # 한 실행에서 반복 첨부 실패는 여기까지만, 나머지는 다음 수집으로 보류

# 파일 자료로 취급하는 활동 모듈 (/mod/<이름>/view.php)
RESOURCE_MODULES = {"resource", "folder", "ubfile"}

# 게시판(ubboard): 이름에 아래 단어가 들어간 것만 수집 — 교수·조교가 올리는 공지/자료실.
# Q&A·팀빌딩·자유게시판처럼 학생 글이 올라오는 곳은 타인 개인정보가 섞이므로 수집하지 않는다.
BOARD_INCLUDE = ("공지", "자료")
BOARD_MAX_POSTS = 30            # 게시판당 최근 글 수 (목록 첫 페이지)

# 동영상(vod) — **재생 화면(mod/vod/view·viewer)은 열지 않는다**(복사방지 무력화 금지). 대신 (2026-10-02 실측)
#   ① 과목 화면의 동영상 옆 표시 '2026-09-01 00:00:00 ~ 2026-09-15 23:59:00, 13:40' → 출석인정 마감(기간 끝)만 마감으로
#   ② 진도 현황(report/ubcompletion/user_progress.php) 의 진도율 ≥ 출석인정 요구시간/길이 → 시청 완료 (과제의 '제출 완료'처럼)
VIDEO_MODULES = {"vod"}
VOD_PROGRESS_URL = f"{BASE_URL}/report/ubcompletion/user_progress.php?id={{course_id}}"
VOD_DONE_PERCENT = 90           # 요구시간을 못 읽으면 이 진도율(%)부터 시청 완료 — 전남대 출석인정 요구시간은 길이의 90%

# 그 외 모듈은 전부 스킵된다. 특히:
#   ubcontent / econtent        … 다른 동영상 모듈 — 제목·링크만 courses.json 에 기록
#   quiz                       … 마감 일시만 (F6-R07 · D3) — 문제·응시 여부는 보지 않는다
#   attendance                 … 출석 모듈 (수집 안 함 — 출결은 F3 가 직접 입력)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ── 종료 코드 (collect · runner — 대시보드가 문구를 고른다) ──
EXIT_OK = 0                     # 성공
EXIT_ERROR = 1                  # 설정·세션 파일·화면 구조 문제 (과목을 못 찾음 등)
EXIT_LOGIN = 2                  # 세션 만료 + 자동 재인증 실패 → 사용자가 로그인 창으로 한 번 (F6-R13)
EXIT_BUSY = 3                   # 다른 실행이 진행 중이라 건너뜀 (재시도 안 함)
EXIT_NETWORK = 4                # 네트워크 없음·일시 오류 → 5·15·45분 재시도 대상 (F6-R12)
EXIT_TIMEOUT = -1               # (백엔드가 띄운 실행이) 시간 초과
EXIT_LAUNCH = -2                # (백엔드가) 실행하지 못함

# ── 주기·재시도 (F6-D1 · R10~R12 · R18) ──
DEFAULT_INTERVAL_HOURS = 4      # 00·04·08·12·16·20시
INTERVAL_CHOICES = (2, 4, 6, 12)
RETRY_WAITS_MIN = (5, 15, 45)   # 네트워크 오류 뒤 재시도 간격 (최대 3회)
CATCHUP_AFTER_MIN = 10          # 정각에서 이만큼 넘게 지나 돌면 '놓친 주기 따라잡기'(catchup)로 기록한다
FAILURE_STREAK_WARN = 3         # 연속 실패가 이 횟수면 빨강 띠 + 알림 1건 (F6-R15)
TASK_NAME = "UnivUs-F6-Eclass-Sync"                # Windows 작업 스케줄러 (register-task.ps1)
LAUNCHD_LABEL = "kr.univus.f6-eclass-sync"         # 맥 launchd 사용자 에이전트 (~/Library/LaunchAgents/<이름>.plist)
LEGACY_TASK_NAMES = ("eClass-Agent-Sync",)          # 예전 eclass_agent 가 등록한 작업 — 새로 등록할 때 지운다

# ── 알림 (F6-D2 · R40~R44) ──
DEFAULT_REMINDERS = ("d3", "d1", "d0")             # D-3 · D-1 · 당일 아침
REMINDER_CHOICES = ("d3", "d1", "d0")
ALERT_TIME = "09:00"                                # 알림 시각
EARLY_DUE_HOURS = 3                                 # 마감이 09:00 이전이면 당일 알림을 마감 3시간 전으로 (F6-R41)
MISSED_AFTER_MIN = 60                               # 예정보다 이만큼 늦게 배달되면 '놓친 알림'

# 과목 색 — courses.json 순서대로 배정 (대시보드 캘린더와 같은 표)
COURSE_PALETTE = [  # 흙빛 팔레트 (디자인 v3 "Paper & Pine" — DESIGN.md)
    "#3d6b8c", "#a0522d", "#7a4f8a", "#2f7a73", "#9a6a14",
    "#a63d52", "#4c5fa6", "#8a6a4c", "#55753a", "#b05a7a",
]


def ensure_dirs() -> None:
    for d in (DATA_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)
