"""지식 베이스 — knowledge/*.json 의 문서·FAQ 를 읽어 검색할 조각(chunk)으로 만든다. 표준 라이브러리만.

자동 수집은 하지 않는다(CG5) — 사람이 정리한 JSON 만 읽는다. 형식은 knowledge/sample.json · README.
    문서  {id, title, sourceUrl, campus, category, dept, verifiedAt, sections: [{section, text}]}
    FAQ   {id, questions[], answer, campus, category, dept, verifiedAt, sourceUrl}
조각 메타는 정의서 6절: docId · section · campus · category · verifiedAt · sourceUrl · dept.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from . import config as C

_SENT = re.compile(r"(?<=[.!?。])\s+|\n+")


def _split(text: str, limit: int) -> list[str]:
    """긴 본문을 문장 경계에서 limit 자 이하로 나눈다."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ""
    for s in (p.strip() for p in _SENT.split(text) if p.strip()):
        while len(s) > limit:                            # 문장 하나가 너무 길면 그냥 자른다
            out, cur, s = out + ([cur] if cur else []) + [s[:limit]], "", s[limit:]
        if cur and len(cur) + 1 + len(s) > limit:
            out, cur = out + [cur], s
        else:
            cur = f"{cur} {s}".strip()
    return out + ([cur] if cur else [])


def _meta(d: dict) -> dict:
    return {"campus": d.get("campus") or "common", "category": d.get("category") or "", "dept": d.get("dept") or "",
            "verifiedAt": d.get("verifiedAt") or "", "sourceUrl": d.get("sourceUrl") or ""}


def chunks() -> list[dict]:
    out: list[dict] = []
    for folder in C.KNOWLEDGE_DIRS:
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                                 # 깨진 파일 하나 때문에 대화가 멈추지 않게
            for doc in data.get("docs") or []:
                for sec in doc.get("sections") or []:
                    for i, part in enumerate(_split(sec.get("text") or "", C.CHUNK_MAX_CHARS)):
                        out.append({"id": f"{doc['id']}#{sec.get('section', '')}#{i}", "kind": "doc", "docId": doc["id"],
                                    "title": doc.get("title") or "", "section": sec.get("section") or "", "text": part,
                                    "match": f"{doc.get('title', '')} {sec.get('section', '')} {part}", **_meta(doc)})
            for faq in data.get("faq") or []:
                qs = faq.get("questions") or []
                out.append({"id": faq["id"], "kind": "faq", "docId": faq["id"], "title": qs[0] if qs else "",
                            "section": "자주 묻는 질문", "text": faq.get("answer") or "",
                            "match": " ".join(qs) + " " + (faq.get("answer") or ""), **_meta(faq)})
    return out


def is_stale(verified_at: str, today: Optional[date] = None) -> bool:
    """검증일이 STALE_DAYS 보다 오래됐나 (CA2-R13). 검증일이 없으면 오래된 것으로 본다."""
    try:
        return ((today or date.today()) - date.fromisoformat(verified_at)).days > C.STALE_DAYS
    except (TypeError, ValueError):
        return True
