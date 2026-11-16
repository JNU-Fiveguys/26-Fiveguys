"""주기 · 잠금 · 실행 이력 · 재시도 사슬 (F6-R10~R16 · R53)."""
import json
import os
from datetime import datetime

import pytest

from eclass import config as C
from eclass import runs as R
from eclass import schedule


def test_slots_and_next():
    assert schedule.slot_hours(4) == [0, 4, 8, 12, 16, 20]
    assert schedule.slot_hours(12) == [0, 12]
    assert schedule.slot_hours(5) == [0, 4, 8, 12, 16, 20]            # 모르는 값은 기본 4시간
    assert schedule.latest_slot(datetime(2026, 9, 28, 13, 59), 4) == datetime(2026, 9, 28, 12, 0)
    assert schedule.next_slot(datetime(2026, 9, 28, 21, 0), 4) == datetime(2026, 9, 29, 0, 0)
    assert schedule.next_slot(datetime(2026, 9, 28, 12, 0), 6) == datetime(2026, 9, 28, 18, 0)


def test_decide_schedule_catchup_and_skip():
    d = schedule.decide(datetime(2026, 9, 28, 12, 1), 4, [])
    assert d["run"] and d["source"] == "schedule"
    d = schedule.decide(datetime(2026, 9, 28, 14, 30), 4, [{"started_at": "2026-09-28T08:00:05", "exit_code": 0}])
    assert d["run"] and d["source"] == "catchup"                      # 12시에 꺼져 있었다 → 켜지면 한 번 (R11)
    d = schedule.decide(datetime(2026, 9, 28, 14, 30), 4, [{"started_at": "2026-09-28T12:40:00", "exit_code": 4}])
    assert not d["run"]                                               # 이번 주기는 이미 시도했다 (로그인 트리거와 겹쳐도 한 번)
    d = schedule.decide(datetime(2026, 9, 28, 14, 30), 4, [{"started_at": "2026-09-28T13:00:00", "exit_code": 3},
                                                          {"started_at": "2026-09-28T09:00:00", "exit_code": 0}])
    assert d["run"]                                                   # 건너뜀(3)은 실행으로 치지 않는다


def test_lock_blocks_second_and_cleans_stale(state):
    assert R.acquire_lock("manual")
    assert not R.acquire_lock("button")                               # 살아 있는 pid (나 자신)
    R.release_lock({"started_at": "2026-09-28T12:00:00", "exit_code": 0, "source": "manual"})
    assert not C.LOCK_FILE.exists()
    C.LOCK_FILE.write_text(json.dumps({"pid": 999999, "started_at": "x"}), encoding="utf-8")
    assert R.acquire_lock("manual")                                   # 죽은 pid 의 잠금은 치운다 (R53)
    assert R.read_lock()[0]["pid"] == os.getpid()
    R.release_lock({"started_at": "2026-09-28T12:05:00", "exit_code": 0})


def test_run_history_and_streak(state):
    for rec in [{"started_at": "2026-09-28T00:00:00", "exit_code": 0, "attempt": 1, "finished_at": "2026-09-28T00:01:00"},
                {"started_at": "2026-09-28T04:00:00", "exit_code": 4, "attempt": 1},
                {"started_at": "2026-09-28T04:05:00", "exit_code": 4, "attempt": 2},
                {"started_at": "2026-09-28T04:20:00", "exit_code": 4, "attempt": 3},
                {"started_at": "2026-09-28T06:00:00", "exit_code": 3, "attempt": 1},
                {"started_at": "2026-09-28T08:00:00", "exit_code": 2, "attempt": 1, "error": "세션 만료"}]:
        R.append_run(rec)
    runs = R.list_runs()
    assert runs[0]["started_at"] == "2026-09-28T08:00:00"
    s = R.failure_streak(runs)
    assert s["count"] == 2                                            # 재시도 사슬은 한 번으로, 건너뜀은 빼고
    assert (s["since"], s["lastCode"], s["lastError"]) == ("2026-09-28T04:00:00", 2, "세션 만료")
    assert s["lastOkAt"] == "2026-09-28T00:01:00"
    assert json.loads(C.LAST_RUN_FILE.read_text(encoding="utf-8"))["exit_code"] == 2


def test_list_runs_falls_back_to_last_json(state):
    C.LAST_RUN_FILE.write_text(json.dumps({"pid": 1, "started_at": "2026-09-27T16:48:47", "exit_code": 0}), encoding="utf-8")
    assert R.list_runs()[0]["started_at"] == "2026-09-27T16:48:47"


def test_settings_interval(state):
    assert R.settings()["intervalHours"] == 4
    assert R.save_settings(intervalHours=6)["intervalHours"] == 6
    C.SETTINGS_FILE.write_text('{"intervalHours": 5}', encoding="utf-8")
    assert R.settings()["intervalHours"] == 4


# ---------------------------------------------------------------- runner (수집은 가짜)

@pytest.fixture()
def fake_collect(monkeypatch, state, db):
    from conftest import assign, write_data
    from eclass import collect, runner
    codes: list[int] = []

    def run(dry_run=False, courses_only=None, only=None):
        code = codes.pop(0)
        if code == 0:
            write_data([assign(1, "2026-10-01 23:59")], stamp=f"s{len(codes)}")
        return collect.Result(code, {"courses": 2}, None if code == 0 else "네트워크 오류", full=True)

    monkeypatch.setattr(collect, "run", run)
    monkeypatch.setattr(C, "RETRY_WAITS_MIN", (0, 0, 0))
    return runner, codes


def test_sync_records_and_reconciles(fake_collect, db):
    runner, codes = fake_collect
    codes += [0]
    assert runner.sync("button") == 0
    r = R.list_runs()[0]
    assert (r["source"], r["exit_code"], r["ledger"]["new"]) == ("button", 0, 1)
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_partial_attachment_success_reconciles_and_reaches_status(fake_collect, db, monkeypatch):
    from eclass import collect, jobs
    runner, codes = fake_collect
    codes += [0]
    original = collect.run

    def run(**kwargs):
        result = original(**kwargs)
        result.partial = True
        result.warnings = ["첨부파일 2건은 다음 동기화에서 다시 시도합니다."]
        result.counts.update(attachmentFailures=1, attachmentPending=2)
        return result

    monkeypatch.setattr(collect, "run", run)
    monkeypatch.setattr(jobs, "_sync", {"running": False, "started_at": None, "finished_at": None, "exit_code": None})
    record = runner.run_once("button")
    assert record["exit_code"] == 0 and record["partial"] and record["error"] is None
    assert record["ledger"]["new"] == 1 and db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    status = jobs.sync_state()
    assert status["partial"] and status["warnings"] == record["warnings"]
    assert status["counts"]["attachmentPending"] == 2 and status["errorKind"] is None


def test_timeout_kind_reaches_status_without_private_error(state, monkeypatch):
    from eclass import collect, jobs, runner
    monkeypatch.setattr(collect, "run", lambda **kw: collect.Result(
        C.EXIT_NETWORK, error="e클래스 요청 시간이 초과되었습니다.", error_kind="timeout"))
    monkeypatch.setattr(jobs, "_sync", {"running": False, "started_at": None, "finished_at": None, "exit_code": None})
    record = runner.run_once("button")
    status = jobs.sync_state()
    assert record["errorKind"] == status["errorKind"] == "timeout"
    assert status["exit_code"] == 4 and not status["partial"] and status["warnings"] == []


def test_tick_retries_network_errors_then_succeeds(fake_collect):
    runner, codes = fake_collect
    codes += [4, 4, 0]
    assert runner.tick(force=True) == 0
    runs = R.list_runs()
    assert [(r["source"], r["attempt"], r["exit_code"]) for r in reversed(runs)] == [
        (runs[-1]["source"], 1, 4), ("retry", 2, 4), ("retry", 3, 0)]
    assert not C.RETRY_FILE.exists()


def test_tick_gives_up_after_three_retries(fake_collect):
    runner, codes = fake_collect
    codes += [4, 4, 4, 4]
    assert runner.tick(force=True) == C.EXIT_NETWORK
    assert len(R.list_runs()) == 4                                    # 첫 시도 + 재시도 3회 (R12)
    assert R.failure_streak(R.list_runs())["count"] == 1


def test_tick_does_not_retry_login_failure(fake_collect):
    runner, codes = fake_collect
    codes += [2]
    assert runner.tick(force=True) == C.EXIT_LOGIN
    assert len(R.list_runs()) == 1


def test_tick_skips_when_slot_done(fake_collect):
    runner, codes = fake_collect
    R.append_run({"started_at": datetime.now().isoformat(timespec="seconds"), "exit_code": 0, "attempt": 1})
    assert runner.tick() == 0 and codes == []                         # 가짜 수집이 불리지 않았다
