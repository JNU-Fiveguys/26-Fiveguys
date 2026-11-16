"""학사 자동 연동: 최신 읽기, 실패 구분, 개인정보 없는 요약, 프로세스 제한."""
import io
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from meal import academic, service
from meal.api import app
from meal.timetable import TimetableError

DAY = date(2026, 11, 11)
REAL_RUN_CLI = academic._run_cli


@pytest.fixture
def exported():
    return {
        "version": 1, "date": DAY.isoformat(),
        "semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
        "blocks": [
            {"kind": "class", "start": "11:00", "end": "11:50", "building": "공7-223", "state": "scheduled"},
            {"kind": "class", "start": "12:00", "end": "13:00", "building": "자연1-205", "state": "canceled"},
            {"kind": "class", "start": "13:30", "end": "14:45", "building": "경영1-101", "state": "scheduled"},
        ],
        "status": "ready", "warnings": [], "updatedAt": "2026-11-11T10:00:00+09:00",
        "courseCount": 3, "missingTimetableCount": 0,
    }


@pytest.fixture
def cli(monkeypatch, exported):
    calls = []
    def read(day, mode):
        calls.append((day, mode))
        return json.dumps(exported, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(academic, "_run_cli", read)
    monkeypatch.delenv("CA1_ACADEMIC_MODE", raising=False)
    return calls


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "load_places", lambda **kwargs: [])
    with TestClient(app) as client:
        yield client


def body(**overrides):
    return {"at": "2026-11-11T11:50:00+09:00", "routeMode": "estimate", **overrides}


def test_status_is_safe_summary_and_counts_only_scheduled_classes(client, cli):
    response = client.get("/v1/meal/timetable?date=2026-11-11")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    summary = response.json()
    assert summary["status"] == "ready" and summary["connected"] is True
    assert summary["dataMode"] == "dev" and summary["blockCount"] == 2
    assert summary["semester"]["id"] == "2026-2"
    assert datetime.fromisoformat(summary["checkedAt"]).utcoffset().total_seconds() == 9 * 3600
    assert cli == [(DAY, "dev")]
    assert set(summary) == {"status", "date", "semester", "courseCount", "missingTimetableCount", "blockCount",
                            "updatedAt", "checkedAt", "warnings", "dataMode", "connected"}
    assert all(private not in response.text for private in ("공7-223", "경영1-101", "building", "blocks", str(academic.REPO_ROOT)))


def test_default_api_uses_fresh_academic_timetable_and_preserves_manual_override(client, cli):
    response = client.post("/v1/meal/recommend", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["context"]["origin"]["code"] == "공7"
    assert result["context"]["destination"]["code"] == "경영1"
    assert result["context"]["end"] == "13:30"
    assert result["academic"]["status"] == "ready"
    manual_room = client.post("/v1/meal/recommend", json=body(nextRoom="자연1-205"))
    assert manual_room.status_code == 200
    assert manual_room.json()["context"]["destination"]["code"] == "자연1"
    assert manual_room.json()["context"]["end"] == "13:30"
    assert len(cli) == 2


def test_manual_mode_and_explicit_development_timetable_never_call_academic_cli(client, exported):
    manual = client.post("/v1/meal/recommend", json=body(autoTimetable=False, origin="공7", nextRoom="경영1", nextStart="14:00"))
    assert manual.status_code == 200, manual.text
    assert manual.json()["academic"]["status"] == "manual"
    provided = client.post("/v1/meal/recommend", json=body(timetable={k: exported[k] for k in ("version", "date", "blocks")}))
    assert provided.status_code == 200
    assert provided.json()["academic"]["status"] == "provided"


@pytest.mark.parametrize("status", ["missing", "no_timetable", "incomplete", "unavailable"])
def test_unusable_status_rejects_auto_even_with_complete_manual_fields_until_explicit_opt_out(client, cli, exported, status):
    exported.update(status=status, blocks=[], courseCount=1 if status == "incomplete" else 0,
                    missingTimetableCount=1 if status == "incomplete" else 0)
    exported["semester"] = {"id": "2026-2", "start": None, "end": None}
    status_response = client.get("/v1/meal/timetable?date=2026-11-11")
    assert status_response.json()["status"] == status
    response = client.post("/v1/meal/recommend", json=body(origin="공7", nextRoom="경영1", nextStart="14:00"))
    assert response.status_code == 422
    assert "직접 입력 모드" in response.json()["detail"]
    manual = client.post("/v1/meal/recommend", json=body(autoTimetable=False, origin="공7", noNextClass=True))
    assert manual.status_code == 200


def test_outside_semester_is_explicit_warning_with_manual_origin(client, cli, exported):
    exported.update(status="outside_semester", blocks=[])
    exported["semester"]["end"] = "2026-10-01"
    exported["warnings"] = ["학기 일정 안내를 확인하세요."]
    response = client.post("/v1/meal/recommend", json=body(origin="공7"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["context"]["destination"] is None
    assert result["academic"]["status"] == "outside_semester"
    assert any("학기 기간 밖" in warning for warning in result["warnings"])
    assert "학기 일정 안내를 확인하세요." in result["warnings"]


def test_ready_day_with_no_classes_is_distinct_from_missing_timetable(client, cli, exported):
    exported["blocks"] = []
    response = client.post("/v1/meal/recommend", json=body(origin="공7"))
    assert response.status_code == 200
    assert response.json()["academic"]["status"] == "ready"
    assert response.json()["context"]["destination"] is None


def test_every_recommendation_reads_changes_and_never_falls_back_to_old_success(client, monkeypatch, tmp_path, exported):
    fixture_file = tmp_path / "synthetic-academic-output.json"
    fixture_file.write_text(json.dumps(exported), encoding="utf-8")
    monkeypatch.setattr(academic, "_run_cli", lambda *args: fixture_file.read_bytes())
    first = client.post("/v1/meal/recommend", json=body()).json()
    old_mtime = fixture_file.stat().st_mtime_ns
    exported["blocks"][2].update(start="14:00", building="자연1-205")
    exported["updatedAt"] = "2026-11-11T11:52:00+09:00"
    fixture_file.write_text(json.dumps(exported), encoding="utf-8")
    os.utime(fixture_file, ns=(old_mtime + 2_000_000_000, old_mtime + 2_000_000_000))
    changed = client.post("/v1/meal/recommend", json=body()).json()
    assert first["context"]["end"] == "13:30" and changed["context"]["end"] == "14:00"
    assert changed["context"]["destination"]["code"] == "자연1"
    assert changed["academic"]["updatedAt"] != first["academic"]["updatedAt"]
    fixture_file.write_text("private invalid contents", encoding="utf-8")
    failed = client.post("/v1/meal/recommend", json=body())
    assert failed.status_code == 422 and "private invalid contents" not in failed.text


@pytest.mark.parametrize("value", ["2026-02-30", "20261111", "not-a-date"])
def test_status_rejects_invalid_dates_without_reading_store(client, value):
    assert client.get("/v1/meal/timetable", params={"date": value}).status_code == 422


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(version=True), lambda d: d.update(status=[]), lambda d: d.update(date="2026-11-12"),
    lambda d: d.update(secret="PRIVATE_COURSE_NAME"), lambda d: d.update(blocks="PRIVATE_PATH"),
    lambda d: d.update(courseCount=True), lambda d: d.update(missingTimetableCount=4),
    lambda d: d.update(updatedAt="PRIVATE_PATH"), lambda d: d.update(warnings=["bad\nwarning"]),
    lambda d: d["semester"].update(id="PRIVATE_NAME"), lambda d: d["semester"].update(start="2026-12-01"),
    lambda d: d["blocks"][0].update(start="29:00"), lambda d: d["blocks"][0].update(start="12:00", end="11:00"),
    lambda d: d["blocks"][0].update(state="absent"), lambda d: d["blocks"][0].update(building=45),
    lambda d: d["blocks"][0].update(courseName="PRIVATE_COURSE_NAME"),
])
def test_malformed_contract_is_unavailable_without_echoing_private_values(client, cli, exported, mutate):
    mutate(exported)
    response = client.get("/v1/meal/timetable?date=2026-11-11")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable" and response.json()["connected"] is False
    assert "PRIVATE" not in response.text


@pytest.mark.parametrize("raw", [b"", b"not json PRIVATE_PATH", b"\xff", b'{"version":1,"version":1}', b'{"version":NaN}', b'{"version":' + b'9' * 5000 + b'}', b" " * (academic.MAX_OUTPUT_BYTES + 1)])
def test_invalid_or_huge_stdout_is_rejected_without_exposing_it(monkeypatch, raw):
    monkeypatch.setattr(academic, "_run_cli", lambda *args: raw)
    snapshot = academic.read_timetable(DAY)
    assert snapshot.summary["status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(snapshot.summary)
    with pytest.raises(TimetableError, match="직접 입력 모드"):
        snapshot.require_usable()


def test_dev_and_app_modes_are_explicit_and_invalid_mode_does_not_fallback(monkeypatch, cli):
    assert "--app" not in academic._command(DAY, "dev")
    app_args = academic._command(DAY, "app")
    assert app_args[:3] == [sys.executable, "-X", "utf8"]
    assert app_args[4:] == ["--app", "attendance", "campus-context", "--date", DAY.isoformat()]
    monkeypatch.setenv("CA1_ACADEMIC_MODE", "app")
    assert academic.read_timetable(DAY).summary["dataMode"] == "app"
    assert cli == [(DAY, "app")]
    monkeypatch.setenv("CA1_ACADEMIC_MODE", "typo")
    failed = academic.read_timetable(DAY)
    assert failed.summary["status"] == "unavailable" and failed.summary["dataMode"] is None
    assert cli == [(DAY, "app")]


def test_subprocess_is_hidden_shell_free_and_preserves_explicit_data_environment(monkeypatch, exported):
    captured = {}
    raw = json.dumps(exported).encode()
    class FakeProcess:
        stdout = io.BytesIO(raw)
        def wait(self, timeout):
            return 0
        def poll(self):
            return 0
    def popen(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return FakeProcess()
    monkeypatch.setenv("F3_DATA_DIR", "EXPLICIT_SYNTHETIC_DIR")
    monkeypatch.setattr(academic.subprocess, "Popen", popen)
    assert REAL_RUN_CLI(DAY, "dev") == raw
    assert captured["shell"] is False and captured["stderr"] == subprocess.DEVNULL
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["env"]["F3_DATA_DIR"] == "EXPLICIT_SYNTHETIC_DIR"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    if os.name == "nt":
        assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert captured["start_new_session"] is True


@pytest.mark.parametrize("script", ["import time; time.sleep(30)", "import sys,time; sys.stdout.buffer.write(b'x'*600000); sys.stdout.flush(); time.sleep(30)"])
def test_real_synthetic_process_timeout_or_oversize_is_terminated_without_temp_output(monkeypatch, script):
    monkeypatch.setattr(academic, "_command", lambda *args: [sys.executable, "-c", script])
    monkeypatch.setattr(academic, "TIMEOUT_SECONDS", 0.15)
    observed = []
    original_terminate = academic._terminate_tree
    def terminate(process):
        original_terminate(process)
        observed.append(process.poll())
    monkeypatch.setattr(academic, "_terminate_tree", terminate)
    started = time.monotonic()
    with pytest.raises(academic.AcademicReadError):
        REAL_RUN_CLI(DAY, "dev")
    assert time.monotonic() - started < 8
    assert observed and observed[0] is not None


def test_os_error_message_is_not_returned_to_browser(monkeypatch):
    def fail(*args):
        raise OSError("PRIVATE_PATH PRIVATE_ACCOUNT")
    monkeypatch.setattr(academic, "_run_cli", fail)
    snapshot = academic.read_timetable(DAY)
    assert snapshot.summary["status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(snapshot.summary)


def test_local_date_matches_minute_rounding_and_korea_timezone():
    assert academic.local_date(datetime(2026, 11, 11, 23, 59, 1)) == date(2026, 11, 12)
    assert academic.local_date(datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)) == date(2026, 11, 12)
