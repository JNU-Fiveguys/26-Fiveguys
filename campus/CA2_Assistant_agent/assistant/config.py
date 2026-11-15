"""CA2 — 설정 (경로 · LLM 슬롯 · 대화 상한). 표준 라이브러리만.

⏳ 값(정의서에서 아직 정하지 않은 값)은 여기 한곳에 두고 환경변수로 바꿀 수 있게 한다 — 코드에 박지 않는다.
LLM 슬롯 이름은 학사 앱과 같다(LLM_MAIN_* · CA0-4). 모델 이름은 코드에 쓰지 않는다(CA2-Q1).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # campus/CA2_Assistant_agent/


def _load_env(path: Path) -> None:
    """.env 의 KEY=VALUE 를 환경변수로 (이미 있는 값은 건드리지 않는다). python-dotenv 없이 — 학사 쪽 F11·F12 와 같다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


DATA_DIR = Path(_env("CA2_DATA_DIR") or ROOT / "data")

# ── LLM 슬롯 (OpenAI 호환 chat/completions) ──
LLM_BASE_URL = _env("LLM_MAIN_BASE_URL").rstrip("/")
LLM_API_KEY = _env("LLM_MAIN_API_KEY")
LLM_MODEL = _env("LLM_MAIN_MODEL")
LLM_TIMEOUT = float(_env("LLM_MAIN_TIMEOUT", "30"))       # 초 — 전체 응답 ⏳8초(12절)는 대화 쪽이 따로 잰다

# ── 임베딩 슬롯 — 주소·키를 비우면 LLM_MAIN_* 것을 쓴다 ──
EMBED_BASE_URL = (_env("LLM_EMBED_BASE_URL") or LLM_BASE_URL).rstrip("/")
EMBED_API_KEY = _env("LLM_EMBED_API_KEY") or LLM_API_KEY
EMBED_MODEL = _env("LLM_EMBED_MODEL")

# ── 대화 상한 ⏳ ──
MAX_TURNS = int(_env("CA2_MAX_TURNS", "6"))               # 서버로 보내는 최근 턴 수 (CA2-R04)
MAX_TOOL_CALLS = int(_env("CA2_MAX_TOOL_CALLS", "4"))     # 한 턴의 도구 호출 (CA2-R25)


def llm_configured() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


def embed_configured() -> bool:
    return bool(EMBED_BASE_URL and EMBED_API_KEY and EMBED_MODEL)

# ── 지식 베이스 · 검색 ⏳ ──
KNOWLEDGE_DIRS = (ROOT / "knowledge", DATA_DIR / "knowledge")   # 저장소에 둔 것 + 이 PC 에서 더한 것
CHUNK_MAX_CHARS = int(_env("CA2_CHUNK_MAX_CHARS", "800"))        # 한 조각 300~800자 (6절)
SEARCH_TOP_K = int(_env("CA2_SEARCH_TOP_K", "8"))                # 근거로 쓰는 상위 조각 (CA2-R10)
SEARCH_MIN_MATCH = float(_env("CA2_SEARCH_MIN_MATCH", "0.3"))    # 질문 낱말 중 이 비율은 맞아야 근거로 쓴다 — 아니면 '확인된 자료가 없어요' (CA2-R12)
STALE_DAYS = int(_env("CA2_STALE_DAYS", "180"))                  # 검증일이 이보다 오래되면 경고 (CA2-R13)
DEFAULT_CAMPUS = _env("CA2_CAMPUS", "gwangju")                   # gwangju · yeosu · hwasun (common 은 언제나 함께 검색)
PENDING_MINUTES = 10                                             # 미리보기 만료 (5절 2단계 쓰기)

# ── 로컬 서버 (프로토타입) — 이 PC 에서만 연다 ──
HOST = "127.0.0.1"
PORT = int(_env("CA2_PORT", "8765"))
WEB_DIR = ROOT / "web"

# ── 강의자료(PDF) 읽기 ⏳ ──
MATERIAL_MAX_PAGES = int(_env("CA2_MATERIAL_MAX_PAGES", "1500"))   # 한 번에 훑는 쪽수 상한 — 넘으면 '너무 많아 확인할 수 없다'
MATERIAL_TOP_PAGES = int(_env("CA2_MATERIAL_TOP_PAGES", "5"))      # LLM 에 근거로 보내는 쪽 수
MATERIAL_PAGE_CHARS = int(_env("CA2_MATERIAL_PAGE_CHARS", "1200")) # 한 쪽에서 보내는 글자 수 상한
TOOL_RESULT_CHARS = int(_env("CA2_TOOL_RESULT_CHARS", "6000"))     # 도구 결과 하나의 크기 상한 (작은 모델·무료 한도)
