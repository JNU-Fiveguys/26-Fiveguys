"""강의자료(PDF) 쪽 단위 검색 — 파일을 쪽별 글자로 읽어 두고(캐시), 질문과 맞는 쪽만 고른다. 출처는 '파일 이름 p.N'.

파일 목록은 부르는 쪽이 준다(이 폴더는 학사 코드를 import 하지 않는다): [{id, title, path, pages?}]
    search(files, query)   → [{id, title, page, text, score}] 상위 MATERIAL_TOP_PAGES 쪽
쪽수 합이 MATERIAL_MAX_PAGES 를 넘으면 TooMany — 다 훑었다고 할 수 없으면 답하지 않는다.
읽어 낸 글자는 이 PC 의 data/pdfcache 에만 둔다(파일 경로·크기·수정 시각이 같으면 다시 읽지 않는다).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from . import config as C
from .search import B, K1, tokens


class TooMany(Exception):
    pass


def _cache_file(path: Path) -> Path:
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()
    return C.DATA_DIR / "pdfcache" / f"{key}.json"


def page_texts(path: Path) -> list[str]:
    """PDF 의 쪽별 글자. 못 읽는 파일(암호·깨짐)은 빈 목록."""
    cache = _cache_file(path)
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        from pypdf import PdfReader
        pages = [" ".join((pg.extract_text() or "").split()) for pg in PdfReader(str(path)).pages]
    except Exception:                                    # noqa: BLE001 — 파일 하나 때문에 대화가 멈추지 않게
        pages = []
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return pages


def search(files: list[dict], query: str) -> dict:
    """→ {hits: [{id, title, page, text, score}], files, pages, unread: [읽지 못한 파일 이름]}"""
    if sum(int(f.get("pages") or 0) for f in files) > C.MATERIAL_MAX_PAGES:
        raise TooMany
    q = set(tokens(query))
    rows, unread, total = [], [], 0
    for f in files:
        texts = page_texts(Path(f["path"]))
        total += len(texts)
        if total > C.MATERIAL_MAX_PAGES:
            raise TooMany
        if not any(texts):
            unread.append(f["title"])                    # 스캔본(글자 없음)·암호 걸린 파일
        rows += [(f, i + 1, t, Counter(tokens(t))) for i, t in enumerate(texts) if t]
    if not rows or not q:
        return {"hits": [], "files": len(files), "pages": total, "unread": unread}
    avg = sum(sum(d.values()) for *_, d in rows) / len(rows)
    df = Counter(t for *_, d in rows for t in d if t in q)
    hits = []
    for f, page, text, d in rows:
        if sum(1 for t in q if t in d) / len(q) < C.SEARCH_MIN_MATCH:
            continue
        n = sum(d.values())
        score = sum(math.log(1 + (len(rows) - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * (K1 + 1)
                    / (d[t] + K1 * (1 - B + B * n / avg)) for t in q if t in d)
        hits.append({"id": f["id"], "title": f["title"], "page": page, "text": text[:C.MATERIAL_PAGE_CHARS], "score": round(score, 3)})
    hits.sort(key=lambda h: -h["score"])
    return {"hits": hits[:C.MATERIAL_TOP_PAGES], "files": len(files), "pages": total, "unread": unread}
