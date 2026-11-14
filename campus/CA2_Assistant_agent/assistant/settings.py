"""LLM 연결 설정 — 사용자가 자기 API 키를 넣으면 바로 쓴다. 키는 OS 암호 저장소(맥 키체인 · 윈도우 자격 증명 관리자)에만 둔다.

    data/settings.json     제공자 · 주소 · 모델 (비밀 아님)
    keyring                API 키 — 파일·로그·API 응답 어디에도 평문으로 남기지 않는다(응답에는 끝 4글자만)

우선순위: 화면에서 저장한 설정 > .env 의 LLM_MAIN_* (개발용). OpenAI 호환 chat/completions 를 주는 곳이면 어디든 된다.
주소를 바꾸면 저장된 키를 지운다 — 예전 키를 새 주소로 보내지 않기 위해서다(키를 같이 넣으면 그 키로 바꾼다).
모델 이름은 코드에 두지 않는다 — 연결한 곳의 /models 목록에서 고르거나 직접 적는다 (modelHint 는 입력 칸의 예시일 뿐).
"""
from __future__ import annotations

import json
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from . import config as C

KEYRING_SERVICE = "kr.univus.ca2"
KEYRING_ACCOUNT = "llm-api-key"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# 주소는 2026-11-14 기준. K-EXAONE 주소는 공식 문서로 직접 확인하지 못했다(외부 글 기준) — 틀리면 화면에서 고쳐 쓴다.
PROVIDERS = [
    {"key": "exaone", "label": "K-EXAONE (LG AI연구원)", "baseUrl": "https://api.exaone.ai/v1", "modelHint": "k-exaone_v2.5"},
    {"key": "gemini", "label": "Google AI Studio (Gemini)", "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"key": "openai", "label": "OpenAI", "baseUrl": "https://api.openai.com/v1"},
    {"key": "upstage", "label": "Upstage Solar", "baseUrl": "https://api.upstage.ai/v1"},
    {"key": "groq", "label": "Groq", "baseUrl": "https://api.groq.com/openai/v1"},
    {"key": "openrouter", "label": "OpenRouter", "baseUrl": "https://openrouter.ai/api/v1"},
    {"key": "ollama", "label": "내 PC (Ollama) — 밖으로 나가지 않음", "baseUrl": "http://localhost:11434/v1"},
    {"key": "custom", "label": "직접 입력 (OpenAI 호환)", "baseUrl": ""},
]


class Invalid(ValueError):
    pass


class Slot(NamedTuple):
    base_url: str
    api_key: str
    model: str
    source: str                                         # settings(화면에서 저장) · env(.env) · none

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def _file():
    return C.DATA_DIR / "settings.json"


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def is_local(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "") in LOCAL_HOSTS


# ── 키 저장소 (테스트는 이 셋을 바꿔 끼운다) ──
def _key_get() -> Optional[str]:
    import keyring
    return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)


def _key_set(value: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)


def _key_delete() -> None:
    import keyring
    import keyring.errors
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except keyring.errors.PasswordDeleteError:           # 없던 키
        pass


def _stored_key() -> str:
    try:
        return _key_get() or ""
    except Exception:                                    # noqa: BLE001 — 키 저장소가 없는 환경(CI 등)에서는 키 없음으로 본다
        return ""


def slot() -> Slot:
    s = _load()
    if s.get("baseUrl") and s.get("model"):
        key = _stored_key() or ("local" if is_local(s["baseUrl"]) else "")     # 내 PC 모델은 키가 필요 없다
        return Slot(s["baseUrl"], key, s["model"], "settings")
    if C.llm_configured():
        return Slot(C.LLM_BASE_URL, C.LLM_API_KEY, C.LLM_MODEL, "env")
    return Slot("", "", "", "none")


def view() -> dict:
    """화면에 주는 모양 — 키는 있는지와 끝 4글자만."""
    s, cur = _load(), slot()
    key = _stored_key() if cur.source == "settings" else ""
    return {"providers": PROVIDERS, "provider": s.get("provider") or ("custom" if cur.source == "env" else ""),
            "baseUrl": cur.base_url, "model": cur.model, "source": cur.source, "configured": cur.ready,
            "hasKey": bool(cur.api_key), "keyHint": f"…{key[-4:]}" if len(key) >= 8 else "", "local": is_local(cur.base_url),
            "materialsConsent": consent()}


def check_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    u = urlsplit(base_url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Invalid("주소는 https://… 모양이어야 합니다")
    if u.scheme == "http" and not is_local(base_url):
        raise Invalid("내 PC(localhost)가 아닌 주소는 https 여야 합니다 — 키가 암호화 없이 나갑니다")
    return base_url


def save(provider: str, base_url: str, model: str, api_key: Optional[str] = None) -> dict:
    base_url = check_url(base_url)
    model = (model or "").strip()
    if not model:
        raise Invalid("모델 이름을 고르거나 적어 주세요")
    api_key = (api_key or "").strip()
    changed = _load().get("baseUrl") != base_url
    if api_key:
        _key_set(api_key)
    elif changed:
        _key_delete()
    if not (api_key or is_local(base_url) or _stored_key()):
        raise Invalid("API 키를 넣어 주세요")
    _write({**_load(), "provider": provider or "custom", "baseUrl": base_url, "model": model})
    return view()


def _write(data: dict) -> None:
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear() -> dict:
    """연결 설정과 키를 지운다 — 강의자료 전송 동의는 따로 관리하므로 남긴다."""
    _key_delete()
    _write({k: v for k, v in _load().items() if k not in ("provider", "baseUrl", "model")})
    return view()


# ── 강의자료 전송 동의 (학사 요구사항 F4-R40 — 원본은 PC 에 남고, 글자 일부가 답을 만들려고 AI 로 나간다는 것을 처음에 알린다) ──
def consent() -> bool:
    return bool(_load().get("materialsConsent"))


def set_consent(agreed: bool) -> bool:
    _write({**_load(), "materialsConsent": bool(agreed)})
    return consent()


def materials_allowed() -> bool:
    """강의자료 본문을 지금 연결한 AI 로 보내도 되나 — 동의했거나, AI 가 내 PC 안에 있을 때."""
    return consent() or is_local(slot().base_url)
