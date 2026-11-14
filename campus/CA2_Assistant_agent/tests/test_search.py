"""지식 조각 · 키워드 검색 — 예시 자료(tests/fixtures/sample.json)와 손으로 만든 조각으로."""
from datetime import date

from assistant import kb, search


def _c(id, text, campus="gwangju", verified="2026-11-01", category=""):
    return {"id": id, "kind": "doc", "docId": id, "title": id, "section": "", "text": text, "match": f"{id} {text}",
            "campus": campus, "category": category, "dept": "", "verifiedAt": verified, "sourceUrl": ""}


def test_sample_knowledge_loads():
    cs = kb.chunks()
    assert {c["kind"] for c in cs} == {"doc", "faq"}
    assert all(c["text"] and c["campus"] and "docId" in c for c in cs)


def test_split_keeps_limit():
    parts = kb._split("가나다라마. " * 100, 80)
    assert len(parts) > 1 and all(0 < len(p) <= 80 for p in parts)
    assert kb._split("짧은 글", 80) == ["짧은 글"] and kb._split("  ", 80) == []


def test_stale():
    today = date(2026, 11, 14)
    assert not kb.is_stale("2026-11-01", today) and kb.is_stale("2026-01-10", today) and kb.is_stale("", today)


def test_finds_by_korean_stem():
    hits = search.search("학생증 재발급 어디서 해?")
    assert hits and hits[0]["section"] == "재발급" and "match" not in hits[0] and hits[0]["score"] > 0


def test_no_hit_returns_empty():
    assert search.search("asdf qwer") == [] and search.search("") == []


def test_campus_filter():
    cs = [_c("광주생활관", "생활관 통금 시간은 01:00 입니다"), _c("여수생활관", "생활관 통금 시간은 23:00 입니다", campus="yeosu"),
          _c("공통", "생활관 문의는 행정실로", campus="common")]
    assert {h["id"] for h in search.search("생활관 통금 시간", campus="gwangju", chunks=cs)} == {"광주생활관", "공통"}
    assert {h["id"] for h in search.search("생활관 통금 시간", campus="yeosu", chunks=cs)} == {"여수생활관", "공통"}


def test_top_k_and_order():
    cs = [_c(f"d{i}", "도서관 열람실 " + "운영 시간 " * i) for i in range(1, 12)]
    hits = search.search("도서관 운영 시간", chunks=cs, k=3)
    assert len(hits) == 3 and [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)
