"""JSON/실행 이력을 원자적으로 갱신한다. Windows의 짧은 읽기 공유 위반만 재시도한다."""
from __future__ import annotations

import errno
import os
import tempfile
import time
from pathlib import Path

_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)  # 최대 6회, 대기 합계 1.55초
_replace = os.replace


def _sharing_violation(exc: PermissionError) -> bool:
    winerror = getattr(exc, "winerror", None)
    return winerror in (5, 32) or (os.name == "nt" and winerror is None and exc.errno == errno.EACCES)


def write_text(path: Path, content: str) -> None:
    """같은 디렉터리의 고유 임시파일을 닫고 교체한다. 실패하면 기존 목적지는 보존한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        # 고정 .tmp 이름을 공유하지 않아 다른 writer의 미완성 내용을 가져오지 않는다.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=f".{path.name}.{os.getpid()}.", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(content)
        for attempt in range(len(_REPLACE_DELAYS) + 1):
            try:
                _replace(tmp, path)
                return
            except PermissionError as exc:
                if not _sharing_violation(exc) or attempt == len(_REPLACE_DELAYS):
                    raise
                time.sleep(_REPLACE_DELAYS[attempt])
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # 정리 실패가 원래 저장 오류를 가리지 않게 한다.
