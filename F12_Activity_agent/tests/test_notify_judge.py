"""알림(내 관심사만 즉시 · 하루 5건 상한 → 묶음 · 담은 것 D-3 · 관심사 없으면 안 보냄)과 LLM 관련도 판단(가짜 LLM)."""
import json
from datetime import datetime

from conftest import add_notice

from activity import judge, notify, service, store

OPEN = "신청기간 : 2026. 10. 1. ~ 2026. 10. 30.(금)\n"
NOW = datetime(2026, 10, 10, 10, 0)


class Pushes(list):
    def __call__(self, nid, ref_id, title, body, href, upsert=False):
        self.append({"nid": nid, "ref": ref_id, "title": title, "body": body, "href": href, "upsert": upsert})


def test_baseline_and_only_match(profile):
    push = Pushes()
    with store.connect() as con:
        add_notice(con, "www:1", "[공모전] 예전 AI 경진대회", OPEN)
        assert notify.deliver(con, profile, push, NOW) == 0                      # 기준선 전 — 알리지 않는다
        store.set_meta(con, "baseline_at", "2026-10-09T00:00:00")
        con.execute("UPDATE notices SET first_seen = '2026-10-08T00:00:00'")
        add_notice(con, "www:2", "[공모전] 2026 AI 경진대회", OPEN)               # 내 관심사
        add_notice(con, "www:3", "[대학생활] 합창단 단원 모집", OPEN)            # 관심 밖
        add_notice(con, "www:4", "청년 포럼", "데이터 이야기\n" + OPEN)           # 관련 있음 — 목록에만
        notify.deliver(con, profile, push, NOW)
        notify.deliver(con, profile, push, NOW)                                   # 두 번 불러도 한 번만
    assert [p["ref"] for p in push] == ["www:2"]
    assert push[0]["href"] == "/opportunities?item=www%3A2" and "AI·SW" in push[0]["body"]


def test_no_interests_marks_seen_without_alert(profile):
    push = Pushes()
    profile["interests"] = None
    with store.connect() as con:
        store.set_meta(con, "baseline_at", "2026-10-09T00:00:00")
        add_notice(con, "www:2", "[공모전] 2026 AI 경진대회", OPEN)
        notify.deliver(con, profile, push, NOW)
        assert push == [] and store.state(con, "www:2")["alerted_at"]
        profile["interests"] = {"tags": ["AI·SW"]}                                # 관심사를 고른 순간 쏟아지지 않는다
        notify.deliver(con, profile, push, NOW)
    assert push == []


def test_daily_cap_bundles(profile):
    push = Pushes()
    with store.connect() as con:
        store.set_meta(con, "baseline_at", "2026-10-09T00:00:00")
        for n in range(8):
            add_notice(con, f"www:{n}", f"[공모전] 2026 AI 경진대회 {n}회", OPEN)
        notify.deliver(con, profile, push, NOW)
        singles = [p for p in push if p["nid"].startswith("opp:new:")]
        bundles = [p for p in push if p["nid"].startswith("opp:bundle:")]
        assert len(singles) == 5 and bundles[-1]["title"] == "관심 기회 3건이 더 있습니다" and bundles[-1]["upsert"]
        add_notice(con, "www:20", "[공모전] 2026 SW 해커톤", OPEN)
        notify.deliver(con, profile, push, NOW)
        assert push[-1]["title"] == "관심 기회 4건이 더 있습니다"                 # 같은 줄을 고쳐 쓴다
        assert json.loads(store.get_meta(con, "alert_day"))["count"] == 5


def test_deadline_d3_saved_only(profile):
    push = Pushes()
    dl = OPEN.replace("10. 30.(금)", "10. 13.(화)")
    with store.connect() as con:
        add_notice(con, "www:1", "[공모전] AI 경진대회", dl)
        add_notice(con, "www:2", "[공모전] SW 경진대회", dl)
        store.set_meta(con, "baseline_at", "2999-01-01T00:00:00")      # 새 글 알림은 빼고 마감 알림만 본다 (오늘 날짜와 무관하게)
        service.patch(con, "www:1", {"saved": True})
        notify.deliver(con, profile, push, NOW)
        notify.deliver(con, profile, push, NOW)
    assert [p["ref"] for p in push] == ["www:1"] and push[0]["title"].startswith("신청 마감 D-3")


def test_llm_judges_only_unhit_open_items(profile):
    asked = []

    def fake(system, user):
        asked.append(user)
        return "높음 - 학생 활동과 맞음"

    with store.connect() as con:
        add_notice(con, "www:1", "[공모전] 2026 AI 경진대회", OPEN)              # 키워드로 이미 맞음 — 묻지 않는다
        add_notice(con, "www:2", "[대학생활] 합창단 단원 모집", OPEN)
        add_notice(con, "www:3", "[대학생활] 지난 모집", OPEN.replace("10. 30.(금)", "10. 1.(목)"))   # 마감 — 묻지 않는다
        n = judge.run_batch(con, profile, fake, today=NOW.date())
        it = {i["id"]: i for i in service.evaluate(con, profile, today=NOW.date())}
    assert n == 1 and len(asked) == 1 and "합창단" in asked[0] and "반도체" in asked[0]
    assert "인공지능학부" not in asked[0]                                          # 관심사와 공지만 보낸다
    assert it["www:2"]["grade"] == "match" and it["www:2"]["by"] == "llm" and it["www:2"]["chips"] == ["LLM 판단"]
