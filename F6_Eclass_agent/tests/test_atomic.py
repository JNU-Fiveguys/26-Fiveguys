"""Windows 읽기 핸들과의 경합, 실패 시 원본 보존, writer 간 임시파일 격리."""
import errno
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from eclass import atomic, collect, runs


def locked_error(code):
    error = PermissionError(errno.EACCES, "busy")
    error.winerror = code
    return error


@pytest.mark.parametrize("winerror", [5, 32])
@pytest.mark.parametrize("save", [collect.save_json, runs.write_json])
def test_transient_windows_replace_retry_keeps_original_until_success(tmp_path, monkeypatch, winerror, save):
    target = tmp_path / "data.json"
    original = '{"old": true}'
    target.write_text(original, encoding="utf-8")
    actual_replace = atomic._replace
    attempts, sleeps = [], []

    def replace(src, dst):
        attempts.append(src)
        assert target.read_text(encoding="utf-8") == original
        assert json.loads(src.read_text(encoding="utf-8")) == {"new": "한글"}
        if len(attempts) < 3:
            raise locked_error(winerror)
        actual_replace(src, dst)

    monkeypatch.setattr(atomic, "_replace", replace)
    monkeypatch.setattr(atomic.time, "sleep", sleeps.append)
    save(target, {"new": "한글"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": "한글"}
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert str(os.getpid()) in attempts[0].name
    assert attempts[0].parent == target.parent and sleeps == [0.05, 0.1]
    assert list(tmp_path.iterdir()) == [target]


def test_persistent_sharing_error_is_bounded_and_original_survives(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    target.write_bytes(b"original bytes")
    attempts, sleeps = [], []

    def replace(src, dst):
        attempts.append(src)
        raise locked_error(32)

    monkeypatch.setattr(atomic, "_replace", replace)
    monkeypatch.setattr(atomic.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError):
        collect.save_json(target, {"new": True})
    assert target.read_bytes() == b"original bytes"
    assert len(attempts) == 6 and sum(sleeps) == pytest.approx(1.55)
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("error", [OSError(errno.ENOSPC, "disk full"), locked_error(1314)])
def test_unrelated_errors_are_not_retried(tmp_path, monkeypatch, error):
    target = tmp_path / "data.json"
    target.write_bytes(b"original")
    attempts = []

    def replace(src, dst):
        attempts.append(src)
        raise error

    monkeypatch.setattr(atomic, "_replace", replace)
    monkeypatch.setattr(atomic.time, "sleep", lambda delay: pytest.fail("일반 오류를 재시도하면 안 됩니다."))
    with pytest.raises(type(error)):
        collect.save_json(target, {"new": True})
    assert len(attempts) == 1 and target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_serialization_failure_does_not_touch_original(tmp_path):
    target = tmp_path / "data.json"
    target.write_bytes(b"original")
    with pytest.raises(TypeError):
        collect.save_json(target, object())
    assert target.read_bytes() == b"original" and list(tmp_path.iterdir()) == [target]


def test_concurrent_writers_use_separate_complete_temporary_files(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    barrier = threading.Barrier(2)
    actual_replace = atomic._replace
    snapshots = []

    def replace(src, dst):
        snapshots.append((src, json.loads(src.read_text(encoding="utf-8"))))
        barrier.wait(timeout=5)
        actual_replace(src, dst)

    monkeypatch.setattr(atomic, "_replace", replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda value: collect.save_json(target, {"value": value}), [1, 2]))
    assert len({src for src, _ in snapshots}) == 2
    assert sorted(snapshot["value"] for _, snapshot in snapshots) == [1, 2]
    assert json.loads(target.read_text(encoding="utf-8")) in ({"value": 1}, {"value": 2})
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="실제 Windows 공유 위반 검증")
def test_real_windows_reader_releases_handle_during_retry(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "data.json"
    target.write_bytes(b"original")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    waiting, released = threading.Event(), threading.Event()
    actual_sleep = atomic.time.sleep
    waits = []

    def release_reader():
        waiting.wait(timeout=5)
        kernel32.CloseHandle(handle)
        released.set()

    def sleep(delay):
        waits.append(delay)
        assert target.read_bytes() == b"original"
        waiting.set()
        assert released.wait(timeout=5)
        actual_sleep(delay)

    thread = threading.Thread(target=release_reader)
    thread.start()
    monkeypatch.setattr(atomic.time, "sleep", sleep)
    try:
        collect.save_json(target, {"recovered": True})
    finally:
        waiting.set()
        thread.join(timeout=5)
    assert waits and released.is_set()
    assert json.loads(target.read_text(encoding="utf-8")) == {"recovered": True}
    assert list(tmp_path.iterdir()) == [target]
