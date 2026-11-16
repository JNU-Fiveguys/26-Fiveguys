"""키워드 검색(BM25) — 표준 라이브러리만. 임베딩 검색은 아직 붙이지 않았다(정의서 D3 의 하이브리드 중 키워드 쪽, 1차).

형태소 분석기 없이 한글을 다루려고 낱말 + 글자 2개 묶음(bigram)을 함께 센다 — '재발급해?' 와 '재발급은' 이 '재발', '발급'으로 만난다.
캠퍼스 필터(CA2-R15): 설정 캠퍼스 + common 만.
근거 문턱(CA2-R12): 질문 낱말 중 SEARCH_MIN_MATCH 비율 이상이 조각에 있어야 한다 — BM25 점수는 자료 수에 따라 크기가 달라져
문턱으로 쓰기 어렵다(자료가 적으면 모든 점수가 작다). 순서는 BM25 점수로 매긴다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from . import config as C
from . import kb

_WORD = re.compile(r"[0-9a-zA-Z가-힣]+")
K1, B = 1.5, 0.75


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text.lower()):
        out.append(w)
        if re.search(r"[가-힣]", w) and len(w) > 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


def search(query: str, campus: Optional[str] = None, category: Optional[str] = None, k: Optional[int] = None,
           chunks: Optional[list[dict]] = None) -> list[dict]:
    """→ 점수 높은 순 [{…조각, score, stale}]. 문턱을 못 넘으면 빈 목록."""
    campus = campus or C.DEFAULT_CAMPUS
    pool = [c for c in (kb.chunks() if chunks is None else chunks)
            if c["campus"] in (campus, "common") and (not category or c["category"] == category)]
    q = set(tokens(query))
    if not pool or not q:
        return []
    docs = [Counter(tokens(c["match"])) for c in pool]
    avg = sum(sum(d.values()) for d in docs) / len(docs)
    df = Counter(t for d in docs for t in d if t in q)
    scored = []
    for c, d in zip(pool, docs):
        n = sum(d.values())
        score = sum(math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * (K1 + 1)
                    / (d[t] + K1 * (1 - B + B * n / avg)) for t in q if t in d)
        if sum(1 for t in q if t in d) / len(q) >= C.SEARCH_MIN_MATCH:
            scored.append({**{x: c[x] for x in c if x != "match"}, "score": round(score, 3), "stale": kb.is_stale(c["verifiedAt"])})
    return sorted(scored, key=lambda c: -c["score"])[: k or C.SEARCH_TOP_K]
