"""첨부 장애는 핵심 정보를 막지 않고 다음 실행에서 복구한다. 외부 요청은 전부 대역이다."""
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from eclass import collect, config as C


FILE = "https://sel.jnu.ac.kr/pluginfile.php/private-course/handout.pdf?token=private-value"
COURSE = {"id": "1", "name": "테스트 과목", "url": "https://sel.jnu.ac.kr/course/view.php?id=1"}
ACT = {"cmid": "7", "name": "테스트 과제", "mod": "assign", "url": "https://sel.jnu.ac.kr/mod/assign/view.php?id=7"}
META = {"course_id": "1", "cmid": "7", "part": "assign"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(C, "DATA_DIR", data)
    for attr, name in (("MANIFEST_FILE", "manifest.json"), ("COURSES_FILE", "courses.json"),
                       ("ASSIGNMENTS_FILE", "assignments.json"), ("DEADLINES_FILE", "deadlines.json")):
        monkeypatch.setattr(C, attr, data / name)
    monkeypatch.setattr(C, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(C, "REQUEST_INTERVAL", 0)
    return data


class Response:
    status = 200

    def __init__(self, url, body=b"pdf", content_type="application/pdf"):
        self.url, self._body = url, body
        self.headers = {"content-type": content_type, "last-modified": "Thu, 01 Oct 2026 04:15:48 GMT"}

    def body(self):
        return self._body

    def text(self):
        return self._body.decode()


class Requests:
    def __init__(self, fail=False):
        self.fail = fail
        self.gets = []
        self.heads = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if url == ACT["url"]:
            html = ('<div id="intro">과제 설명<a href="' + FILE + '">첨부</a></div>'
                    '<table class="generaltable"><tr><th>종료 일시</th><td>2026-11-20 23:59</td></tr></table>')
            return Response(url, html.encode(), "text/html")
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            raise TimeoutError("Timeout 60000: " + url)
        return Response(url, url.encode())

    def fetch(self, url, **kwargs):
        self.heads.append(url)
        if self.fail:
            raise TimeoutError("HEAD Timeout: " + url)
        return Response(url)

    def dispose(self):
        pass


def sync(req, manifest=None):
    return collect.Sync(collect.Client(req), manifest or collect.load_manifest(), False)


def test_timeout_is_queued_once_without_private_error_output(isolated, capsys):
    req = Requests(fail=True)
    s = sync(req)
    dest = isolated / "handout.pdf"
    assert s.fetch_file(FILE, dest, META) is None
    assert s.fetch_file(FILE, dest, META) is None
    s.retry_pending_files([COURSE], {"assign"})
    assert req.gets == [FILE]
    assert not dest.exists()
    queued = collect.load_manifest()["pending_files"][collect.file_key(FILE)]
    assert queued["operation"] == "download" and queued["attempts"] == 1
    assert collect._counts(s, s.c)["attachmentFailures"] == 1
    output = capsys.readouterr().out
    assert "private-course" not in output and "private-value" not in output and "handout.pdf" not in output


def test_saved_post_attachment_retried_after_restart_without_redownload(isolated):
    s = sync(Requests(fail=True))
    meta = {**META, "part": "boards", "post": "공지"}
    s.fetch_file(FILE, isolated / "handout.pdf", meta)
    md = isolated / "post.md"
    md.write_text("# 공지\n\n## 본문\n\n본문 유지\n", encoding="utf-8")
    s.manifest["posts"]["board:post"] = {"path": collect.rel_path(md), "attachments": [],
                                          "attachment_keys": [collect.file_key(FILE)]}
    s.save_manifest()
    req = Requests()
    restarted = sync(req)
    restarted.retry_pending_files([COURSE], {"boards"})
    assert req.gets == [FILE]
    assert not restarted.manifest["pending_files"]
    post = restarted.manifest["posts"]["board:post"]
    assert post["attachments"] == [collect.rel_path(isolated / "handout.pdf")]
    assert "## 첨부" in md.read_text(encoding="utf-8") and "본문 유지" in md.read_text(encoding="utf-8")
    third = sync(req)
    third.retry_pending_files([COURSE], {"boards"})
    third.fetch_file(FILE, isolated / "handout.pdf", meta)
    assert req.gets == [FILE]


def test_head_failure_preserves_download_and_retries_metadata_only(isolated):
    dest = isolated / "existing.pdf"
    dest.write_bytes(b"original")
    entry = {"url": FILE, "path": collect.rel_path(dest), "size": 8, **META}
    s = sync(Requests(fail=True), {"files": {collect.file_key(FILE): entry}, "posts": {}})
    assert s.fetch_file(FILE, dest, META) == collect.rel_path(dest)
    assert s.c.req.gets == []
    assert s.manifest["pending_files"][collect.file_key(FILE)]["operation"] == "metadata"
    req = Requests()
    restarted = sync(req)
    restarted.retry_pending_files([COURSE], {"assign"})
    assert req.gets == [] and req.heads == [FILE]
    assert not restarted.manifest["pending_files"] and dest.read_bytes() == b"original"


def test_attachment_session_expiry_still_stops_collection(isolated):
    s = sync(Requests(fail=collect.SessionExpired("private-session-url")))
    with pytest.raises(collect.SessionExpired):
        s.fetch_file(FILE, isolated / "file.pdf", META)
    assert not s.manifest["pending_files"]


def test_retry_budget_gives_deferred_file_priority_next_run(isolated):
    urls = [f"https://sel.jnu.ac.kr/pluginfile.php/{i}/file.pdf" for i in range(4)]
    first = sync(Requests(fail=True))
    for i, url in enumerate(urls):
        first.fetch_file(url, isolated / f"{i}.pdf", META)
    assert first.c.req.gets == urls[:3]
    second_req = Requests()
    second = sync(second_req)
    second.retry_pending_files([COURSE], {"assign"})
    assert second_req.gets[0] == urls[3]
    for i, url in enumerate(urls):
        second.fetch_file(url, isolated / f"{i}.pdf", META)
    assert len(second_req.gets) == 4 and not second.manifest["pending_files"]


def test_different_urls_with_same_filename_keep_both_files(isolated):
    s = sync(Requests())
    a = "https://sel.jnu.ac.kr/pluginfile.php/1/shared.pdf"
    b = "https://sel.jnu.ac.kr/pluginfile.php/2/shared.pdf"
    dest = isolated / "shared.pdf"
    first = s.fetch_file(a, dest, META)
    second = s.fetch_file(b, dest, META)
    assert first != second
    assert collect.abs_path(first).read_bytes() == a.encode()
    assert collect.abs_path(second).read_bytes() == b.encode()
    s.fetch_file(a, dest, META)
    s.fetch_file(b, dest, META)
    assert s.c.req.gets == [a, b]


@pytest.mark.skipif(os.name != "nt", reason="Windows 파일명 대소문자 충돌")
def test_windows_case_only_filename_difference_keeps_both(isolated):
    s = sync(Requests())
    a = "https://sel.jnu.ac.kr/pluginfile.php/1/Report.pdf"
    b = "https://sel.jnu.ac.kr/pluginfile.php/2/report.pdf"
    first = s.fetch_file(a, isolated / "Report.pdf", META)
    second = s.fetch_file(b, isolated / "report.pdf", META)
    assert collect.abs_path(first).read_bytes() == a.encode()
    assert collect.abs_path(second).read_bytes() == b.encode()


def test_inactive_course_queue_is_preserved_without_permanent_partial(isolated):
    s = sync(Requests(fail=True))
    s.fetch_file(FILE, isolated / "file.pdf", {**META, "course_id": "inactive"})
    restarted = sync(Requests())
    restarted.retry_pending_files([COURSE], {"assign"})
    assert restarted.manifest["pending_files"] and not restarted.c.req.gets
    assert collect._counts(restarted, restarted.c)["attachmentPending"] == 0


@pytest.mark.parametrize("kind", ["media", "oversized"])
def test_policy_exclusion_removes_pending_without_repeated_download(isolated, kind):
    s = sync(Requests(fail=True))
    s.fetch_file(FILE, isolated / "file.pdf", META)
    req = Requests()
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = Response(url)
        if kind == "media":
            response.headers["content-type"] = "video/mp4"
        else:
            response.headers["content-length"] = str(C.MAX_FILE_MB * 1024 * 1024 + 1)
        return response

    req.get = get
    recovered = sync(req)
    recovered.retry_pending_files([COURSE], {"assign"})
    assert not recovered.manifest["pending_files"] and not (isolated / "file.pdf").exists()
    sync(req).fetch_file(FILE, isolated / "file.pdf", META)
    assert calls == [FILE]


@pytest.fixture
def run_offline(isolated, monkeypatch):
    import playwright.sync_api
    credential_placeholder = isolated / "fake-login-record"
    credential_placeholder.touch()
    login = SimpleNamespace(STATE_FILE=isolated / "absent-state", CRED_FILE=credential_placeholder,
                            config=SimpleNamespace(use_browsers=lambda: None), session_ok=lambda req: True)
    monkeypatch.setattr(collect, "_c3_login", lambda: login)
    monkeypatch.setattr(collect, "_probe", lambda p: None)
    monkeypatch.setattr(collect, "find_courses", lambda c: [COURSE])
    monkeypatch.setattr(collect, "find_activities", lambda c, course: {"7": ACT})
    monkeypatch.setattr(collect.Sync, "collect_calendar", lambda s: [])

    def run(req):
        p = SimpleNamespace(request=SimpleNamespace(new_context=lambda **kw: req))
        monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: nullcontext(p))
        return collect.run()
    return run


def test_full_run_saves_core_metadata_despite_attachment_timeout_then_recovers(run_offline):
    failed = run_offline(Requests(fail=True))
    assert failed.code == C.EXIT_OK and failed.full and failed.partial and failed.error is None
    assert failed.counts["attachmentPending"] == 1 and failed.counts["attachmentFailures"] == 1
    assert failed.warnings and len(json.loads(C.COURSES_FILE.read_text(encoding="utf-8"))) == 1
    assignments = json.loads(C.ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
    assert assignments[0]["due"] == "2026-11-20 23:59" and assignments[0]["attachments"] == []
    assert len(json.loads(C.DEADLINES_FILE.read_text(encoding="utf-8"))["items"]) == 1
    req = Requests()
    recovered = run_offline(req)
    assert recovered.code == 0 and recovered.full and not recovered.partial
    assert recovered.counts["attachmentPending"] == 0 and req.gets.count(FILE) == 1
    assert json.loads(C.ASSIGNMENTS_FILE.read_text(encoding="utf-8"))[0]["attachments"]


def test_core_timeout_still_fails_with_safe_error_kind(run_offline, monkeypatch):
    def broken(c, course):
        raise TimeoutError("Timeout 60000 https://sel.jnu.ac.kr/private?token=secret")
    monkeypatch.setattr(collect, "find_activities", broken)
    result = run_offline(Requests())
    assert result.code == C.EXIT_NETWORK and result.error_kind == "timeout" and not result.full
    assert "https" not in result.error and "secret" not in result.error
    assert not C.ASSIGNMENTS_FILE.exists()


def test_core_soft_error_disables_removal_reconciliation(run_offline, monkeypatch):
    def broken(*args):
        raise ValueError("unexpected page shape")
    monkeypatch.setattr(collect.Sync, "collect_assignment", broken)
    result = run_offline(Requests())
    assert result.code == C.EXIT_OK and not result.full and result.counts["errors"] == 1


@pytest.mark.parametrize(("status", "content_type"), [(404, ""), (500, "application/octet-stream")])
def test_non_html_http_error_is_not_saved_as_a_download_and_can_recover(isolated, status, content_type):
    req = Requests()
    response = Response(FILE, b"server error body", content_type)
    response.status = status
    response.body = lambda: pytest.fail("HTTP 오류 본문을 파일로 읽으면 안 됩니다.")
    req.get = lambda *args, **kwargs: response
    failed = sync(req)
    dest = isolated / "handout.pdf"

    assert failed.fetch_file(FILE, dest, META) is None
    key = collect.file_key(FILE)
    assert not dest.exists() and key not in failed.manifest["files"]
    assert failed.manifest["pending_files"][key]["reason"] == "unavailable"
    assert key not in failed.manifest.get("skipped_files", {})

    recovered = sync(Requests())
    recovered.retry_pending_files([COURSE], {"assign"})
    assert not recovered.manifest["pending_files"]
    assert dest.read_bytes() == FILE.encode()
    assert recovered.c.req.gets == [FILE]


def test_post_attachment_recovery_updates_same_feed_item_without_new_alert(isolated):
    import sqlite3
    from datetime import datetime, timedelta
    from eclass import feed, store

    now = datetime(2026, 11, 11, 12, 0)
    s = sync(Requests(fail=True))
    s.save_manifest()
    # 실제 앱 DB 대신 이 테스트의 임시 디렉터리에만 원장을 만든다.
    con = sqlite3.connect(isolated / "feed-test.sqlite")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(store.SCHEMA)
        assert feed.sync(con, now)["baseline"] == 0

        meta = {**META, "course": COURSE["name"], "part": "boards", "post": "공지", "activity": "공지사항"}
        s.fetch_file(FILE, isolated / "handout.pdf", meta)
        md = isolated / "post.md"
        md.write_text("# 공지\n\n## 본문\n\n본문 유지\n", encoding="utf-8")
        s.manifest["posts"]["7:1"] = {
            **meta, "path": collect.rel_path(md), "title": "공지", "date": "2026-11-11 12:00",
            "url": "https://sel.jnu.ac.kr/mod/ubboard/article.php?id=7&bwid=1",
            "fetched_at": now.isoformat(), "attachments": [], "attachment_keys": [collect.file_key(FILE)],
        }
        s.save_manifest()
        assert feed.sync(con, now)["new"] == 1
        sent = []

        def push(*args):
            sent.append(args)
            return True

        assert feed.deliver(con, push, now) == 1
        assert feed.mark_read(con, ["post:7:1"]) == 1
        before = dict(con.execute("SELECT * FROM feed WHERE id = 'post:7:1'").fetchone())

        restarted = sync(Requests())
        restarted.retry_pending_files([COURSE], {"boards"})
        assert not restarted.manifest["pending_files"]
        assert feed.sync(con, now + timedelta(minutes=5))["new"] == 0
        rows = con.execute("SELECT * FROM feed").fetchall()
        assert len(rows) == 1  # 복구한 게시글 첨부가 별도 강의자료로 추가되지 않는다.
        after = rows[0]
        assert after["id"] == "post:7:1" and after["kind"] == "notice"
        assert json.loads(after["attachments"]) == ["handout.pdf"]
        assert all(after[key] == before[key] for key in ("first_seen", "read_at", "notified", "baseline"))
        assert feed.detail(con, "post:7:1")["body"] == "본문 유지"
        assert feed.deliver(con, push, now + timedelta(minutes=5)) == 0
        assert len(sent) == 1 and feed.summary(con)["unread"] == 0
    finally:
        con.close()
