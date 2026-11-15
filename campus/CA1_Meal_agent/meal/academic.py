"""학사 CLI에서 필요한 하루 시간표만 메모리로 읽는다. 개인 자료를 저장하거나 로그하지 않는다."""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .timetable import SEOUL, TimetableError

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = REPO_ROOT / "desktop" / "cli.py"
MAX_OUTPUT_BYTES = 512 * 1024
TIMEOUT_SECONDS = 8.0
_CLI_LOCK = threading.Lock()
STATUSES = {"ready", "incomplete", "no_timetable", "missing", "outside_semester", "unavailable"}
STATUS_MESSAGES = {
    "incomplete": "학사 앱의 시간표가 일부 비어 있습니다. 학사 앱에서 시간표를 완성하거나 직접 입력 모드를 선택하세요.",
    "no_timetable": "학사 앱에 사용할 시간표가 없습니다. 시간표를 등록하거나 직접 입력 모드를 선택하세요.",
    "missing": "학사 앱의 저장된 시간표를 찾지 못했습니다. 학사 앱을 설정하거나 직접 입력 모드를 선택하세요.",
    "outside_semester": "선택한 날짜가 학기 기간 밖입니다. 출발 위치와 필요한 다음 일정을 직접 확인하세요.",
    "unavailable": "학사 시간표를 읽지 못했습니다. 학사 앱 상태를 확인하거나 직접 입력 모드를 선택하세요.",
}


class AcademicReadError(ValueError):
    """출력 내용·경로를 포함하지 않는 안전한 연동 실패."""


@dataclass(frozen=True)
class AcademicSnapshot:
    context: dict
    summary: dict

    def require_usable(self) -> dict:
        if self.summary["status"] not in {"ready", "outside_semester"}:
            raise TimetableError(STATUS_MESSAGES.get(self.summary["status"], STATUS_MESSAGES["unavailable"]))
        return self.context


def local_date(at: datetime) -> date:
    local = at.replace(tzinfo=SEOUL) if at.tzinfo is None else at.astimezone(SEOUL)
    # 시간표 해석과 같은 분 올림을 적용해 자정 직전 요청의 날짜도 일치시킨다.
    if local.second or local.microsecond:
        from datetime import timedelta
        local = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return local.date()


def _day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise AcademicReadError("시간표 날짜 형식이 올바르지 않습니다.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AcademicReadError("시간표 날짜 형식이 올바르지 않습니다.") from exc


def _mode() -> str:
    mode = os.environ.get("CA1_ACADEMIC_MODE", "dev").strip().lower()
    if mode not in {"dev", "app"}:
        raise AcademicReadError("학사 연동 모드는 dev 또는 app이어야 합니다.")
    return mode


def _command(day: date, mode: str) -> list[str]:
    if not CLI_PATH.is_file():
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    return [sys.executable, "-X", "utf8", str(CLI_PATH), *(["--app"] if mode == "app" else []),
            "attendance", "campus-context", "--date", day.isoformat()]


def _terminate_tree(process) -> None:
    """CLI가 만든 기능 프로세스까지 종료한다. 다른 학사 앱 프로세스는 건드리지 않는다."""
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_cli(day: date, mode: str) -> bytes:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    process = subprocess.Popen(_command(day, mode), cwd=str(REPO_ROOT), env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               shell=False, **options)
    output = queue.Queue(maxsize=1)
    def read_bounded():
        try:
            output.put(process.stdout.read(MAX_OUTPUT_BYTES + 1))
        except (OSError, ValueError):
            output.put(None)
    reader = threading.Thread(target=read_bounded, daemon=True)
    reader.start()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        raw = output.get(timeout=TIMEOUT_SECONDS)
        if raw is None or len(raw) > MAX_OUTPUT_BYTES:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if returncode != 0:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        return raw
    except (queue.Empty, subprocess.TimeoutExpired) as exc:
        raise AcademicReadError("학사 시간표 응답 시간이 초과되었습니다. 잠시 후 다시 시도하거나 직접 입력 모드를 선택하세요.") from exc
    finally:
        if process.poll() is None:
            _terminate_tree(process)
        reader.join(timeout=0.5)
        if not reader.is_alive():
            process.stdout.close()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        result[key] = value
    return result


def _reject_constant(value):
    raise AcademicReadError(STATUS_MESSAGES["unavailable"])


def _parse(raw: bytes, day: date) -> dict:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_OUTPUT_BYTES:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"]) from exc
    required = {"version", "date", "semester", "blocks", "status", "warnings", "updatedAt", "courseCount", "missingTimetableCount"}
    if not isinstance(data, dict) or set(data) != required or type(data["version"]) is not int or data["version"] != 1:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if _day(data["date"]) != day or data["status"] not in STATUSES:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    semester = data["semester"]
    if not isinstance(semester, dict) or set(semester) != {"id", "start", "end"}:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if semester["id"] is not None and (not isinstance(semester["id"], str) or not re.fullmatch(r"\d{4}-[12SW]", semester["id"])):
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    bounds = {k: _day(semester[k]) if semester[k] is not None else None for k in ("start", "end")}
    if bounds["start"] and bounds["end"] and bounds["start"] > bounds["end"]:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    for key in ("courseCount", "missingTimetableCount"):
        if type(data[key]) is not int or not 0 <= data[key] <= 10000:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if data["missingTimetableCount"] > data["courseCount"]:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if not isinstance(data["warnings"], list) or len(data["warnings"]) > 30 or any(
        not isinstance(w, str) or len(w) > 500 or any(ord(c) < 32 for c in w) for w in data["warnings"]
    ):
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    updated = data["updatedAt"]
    if updated is not None:
        try:
            if not isinstance(updated, str) or len(updated) > 40:
                raise ValueError
            datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"]) from exc
    if not isinstance(data["blocks"], list) or len(data["blocks"]) > 500:
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    for block in data["blocks"]:
        if not isinstance(block, dict) or set(block) != {"kind", "start", "end", "building", "state"}:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        if block["kind"] != "class" or block["state"] not in {"scheduled", "canceled"}:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        if any(not isinstance(block[k], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", block[k]) for k in ("start", "end")) or block["start"] >= block["end"]:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        if not isinstance(block["building"], str) or len(block["building"]) > 120:
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if data["status"] == "ready" and (not all(bounds.values()) or not bounds["start"] <= day <= bounds["end"] or data["missingTimetableCount"] or not data["courseCount"]):
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    if data["status"] == "outside_semester" and (not all(bounds.values()) or bounds["start"] <= day <= bounds["end"] or data["blocks"]):
        raise AcademicReadError(STATUS_MESSAGES["unavailable"])
    return data


def snapshot_from_context(data: dict, day: date, *, mode: str = "app") -> AcademicSnapshot:
    """앱 안의 F3 읽기 전용 exporter 결과도 CLI와 같은 계약으로 검사한다."""
    selected = _day(day.isoformat())
    data = _parse(json.dumps(data, ensure_ascii=False).encode("utf-8"), selected)
    warnings = list(dict.fromkeys(data["warnings"] + ([STATUS_MESSAGES[data["status"]]] if data["status"] in STATUS_MESSAGES else [])))
    summary = {k: data[k] for k in ("status", "date", "semester", "courseCount", "missingTimetableCount", "updatedAt")}
    summary.update(blockCount=sum(block["state"] == "scheduled" for block in data["blocks"]),
                   checkedAt=datetime.now(SEOUL).isoformat(timespec="seconds"), warnings=warnings,
                   dataMode=mode, connected=data["status"] not in {"missing", "unavailable"})
    return AcademicSnapshot({"version": 1, "date": selected.isoformat(), "blocks": data["blocks"]}, summary)


def read_timetable(day: date) -> AcademicSnapshot:
    """캐시 없이 최신 학사 저장값을 읽는다. 실패를 지난 시간표로 대체하지 않는다."""
    selected = _day(day.isoformat())
    mode = None
    checked_at = datetime.now(SEOUL).isoformat(timespec="seconds")
    try:
        mode = _mode()
        if not _CLI_LOCK.acquire(timeout=TIMEOUT_SECONDS):
            raise AcademicReadError(STATUS_MESSAGES["unavailable"])
        try:
            data = _parse(_run_cli(selected, mode), selected)
        finally:
            _CLI_LOCK.release()
        return snapshot_from_context(data, selected, mode=mode)
    except (AcademicReadError, OSError, subprocess.SubprocessError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, AcademicReadError) else STATUS_MESSAGES["unavailable"]
        summary = {"status": "unavailable", "date": selected.isoformat(), "semester": None, "courseCount": 0,
                   "missingTimetableCount": 0, "blockCount": 0, "updatedAt": None, "checkedAt": checked_at,
                   "warnings": [message], "dataMode": mode, "connected": False}
        return AcademicSnapshot({"version": 1, "date": selected.isoformat(), "blocks": []}, summary)


def explicit_summary(day: date, *, provided: bool) -> dict:
    mode = "provided" if provided else "manual"
    return {"status": mode, "date": day.isoformat(), "semester": None, "courseCount": 0,
            "missingTimetableCount": 0, "blockCount": 0, "updatedAt": None,
            "checkedAt": datetime.now(SEOUL).isoformat(timespec="seconds"), "warnings": [],
            "dataMode": mode, "connected": False}
