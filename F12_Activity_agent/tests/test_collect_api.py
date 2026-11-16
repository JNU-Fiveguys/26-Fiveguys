"""수집 파이프라인(가짜 수집기 — 학교 사이트에 접속하지 않는다) · 학과 공지 게시판 찾기 · F11 기다리기 · API."""
import json
import os
from types import SimpleNamespace

import pytest

from activity import collect, config as C, service, store
from scholarship.sources.base import Post


class FakeHttp:
    """URL → HTML. 요청 수를 센다."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.count = 0

    def get(self, url, **kw):
        self.count += 1
        return SimpleNamespace(text=self.pages[url], url=url)


def fake_board(posts, details):
    def list_posts(cfg, http, pages):
        return list(posts)

    def fetch_detail(post, cfg, http):
        post.body = details.get(post.native_id, "신청기간 : 2026. 10. 1. ~ 2026. 10. 30.")
        return post
    return SimpleNamespace(list_posts=list_posts, fetch_detail=fetch_detail)


@pytest.fixture
def board(monkeypatch):
    from scholarship import sources
    posts = [
        Post("101", "[모집공고] 2026 AI 해커톤 참가자 모집", "https://x.invalid/101", "2026-10-08"),
        Post("102", "[장학안내] 2026 두을장학재단 장학생 선발", "https://x.invalid/102", "2026-10-08"),
        Post("103", "[채용공고] 국제협력과 기간제 계약직 직원 채용", "https://x.invalid/103", "2026-10-08"),
        Post("104", "[대학생활] 유사종교 포교 활동 주의 안내", "https://x.invalid/104", "2020-09-04", pinned=True),
        Post("105", "[취업정보] [KIA] 신입 채용", "https://x.invalid/105", "2026-10-07"),
    ]
    monkeypatch.setitem(sources.COLLECTORS, "jnu_board", fake_board(posts, {}))
    return posts


def test_collect_keeps_non_scholarship_recent(board):
    cfg = {"key": "jnu_notice", "kind": "jnu_board", "base": "https://www.jnu.ac.kr", "name": "전남대 공지"}
    c = collect.collect_source(cfg, FakeHttp({}), {})
    assert c["matched"] == 2 and c["new"] == 2
    assert c["skipped"] == {"장학": 1, "말머리 채용공고": 1, "오래된 글": 1}
    with store.connect() as con:
        ids = sorted(r["id"] for r in con.execute("SELECT id FROM notices"))
        info = json.loads(store.notice(con, "www:101")["info"])
    assert ids == ["www:101", "www:105"]
    assert info["category"] == "contest" and info["period"]["end"] == "2026-10-30"


def test_run_writes_baseline_and_last(board, monkeypatch):
    monkeypatch.setattr("activity.registry.collectable",
                        lambda rows: [r for r in rows if r["key"] == "jnu_notice"])
    assert collect.run(None, {"college": "AI융합대학", "department": "인공지능학부"}) == 0
    with store.connect() as con:
        assert store.get_meta(con, "baseline_at") and store.source_rows(con)["jnu_notice"]["last_count"] == 2
    last = json.loads(C.LAST_RUN_FILE.read_text(encoding="utf-8"))
    assert last["exit_code"] == 0 and not C.LOCK_FILE.exists()


HOME = """<html><body>
<a href="/aisw/8123/subview.do">장학게시판</a>
<a href="/aisw/8100/subview.do">학부공지</a>
<a href="/aisw/8200/subview.do">대학원공지</a>
</body></html>"""


def test_discover_notice_skips_scholarship_menu():
    http = FakeHttp({"https://aisw.example.invalid/": HOME,
                     "https://aisw.example.invalid/aisw/8100/subview.do": '<a href="/bbs/aisw/64/artclList.do">목록</a>'})
    d = collect.discover_notice("https://aisw.example.invalid/", http)
    assert (d["site"], d["board"], d["boardLabel"]) == ("aisw", "64", "학부공지")
    assert http.count == 2                                       # 장학 메뉴는 열어 보지도 않는다


def test_wait_for_f11_lock():
    C.F11_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    C.F11_LOCK_FILE.write_text(json.dumps({"pid": os.getpid() + 1, "started_at": "x"}), encoding="utf-8")
    alive = iter([True, True, False])
    slept = []
    orig = C.pid_alive
    C.pid_alive = lambda pid: next(alive)
    try:
        assert collect.wait_for_peer(sleep=slept.append) is True
    finally:
        C.pid_alive = orig
        C.F11_LOCK_FILE.unlink()
    assert slept == [5, 5]
    assert collect.wait_for_peer(sleep=slept.append) is False      # 잠금이 없으면 바로


def test_api_flow(profile, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from activity import api
    from conftest import TODAY, FakeCalendar, add_notice

    class FixedDate(service.date):
        @classmethod
        def today(cls):
            return TODAY

    # API는 날짜 인자가 없으므로 예시 공지의 모집 기간 안으로 테스트 시계만 고정한다.
    monkeypatch.setattr(service, "date", FixedDate)
    cal = FakeCalendar()
    app = FastAPI()
    app.include_router(api.build_router(lambda: profile, cal.add, cal.remove, lambda: {"careerGoal": "장학 재료"}))
    c = TestClient(app)
    with store.connect() as con:
        add_notice(con, "www:1", "[공모전] 2026 AI 경진대회", "신청기간 : 2026. 10. 1. ~ 2026. 10. 30.")
    ov = c.get("/api/opportunities").json()
    assert ov["counts"]["match"] == 1 and ov["tags"][0]["key"] == "AI·SW" and ov["interests"]["set"]
    assert c.patch("/api/opportunities/items/www:1", json={"saved": True}).json()["onCalendar"]
    assert c.post("/api/opportunities/items/www:1/draft", json={}).status_code == 409
    m = c.get("/api/opportunities/materials").json()
    assert m["from"] == "scholarship" and m["careerGoal"] == "장학 재료"
    c.put("/api/opportunities/materials", json={**{k: m[k] for k in ("careerGoal", "activities", "skills", "highlights")}})
    d = c.post("/api/opportunities/items/www:1/draft", json={}).json()
    assert d["progress"] == "draft" and "장학 재료" in d["draft"]["body"]
    src = c.get("/api/opportunities/sources").json()["sources"]
    assert "jnu_scholarship" not in [s["key"] for s in src]
    assert c.patch("/api/opportunities/sources/grow", json={"enabled": False}).json()["enabled"] is False
    assert c.get("/api/opportunities/items/nope").status_code == 404
    assert c.get("/api/opportunities/history").json()["semesters"][0]["saved"] == 1
