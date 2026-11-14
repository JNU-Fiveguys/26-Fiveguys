"""보내기 전 개인정보 확인 (CA2-R32) — 학번·주민등록번호·비밀번호처럼 보이는 것이 있으면 LLM 으로 보내기 전에 한 번 묻는다."""
from __future__ import annotations

import re

_CHECKS = (
    ("주민등록번호", re.compile(r"\b\d{6}\s*-\s*[1-4]\d{6}\b")),
    ("학번", re.compile(r"학번\D{0,6}\d{5,10}|\b\d{5,10}\s*(?:학번|번\s*학생)")),
    ("비밀번호", re.compile(r"(?:비밀번호|비번|패스워드|password)\s*(?:은|는|이|가|:|=)?\s*\S{4,}", re.I)),
)


def find(text: str) -> list[str]:
    return [label for label, pat in _CHECKS if pat.search(text or "")]
