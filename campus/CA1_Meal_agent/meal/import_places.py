"""공공 후보 CSV를 조사 내용을 보존하는 places JSON으로 가져온다.

sourceId만 가게의 식별자로 사용한다. 이름·주소·좌표가 같아도 합치지 않는다.
입력은 선정된 후보 목록의 전체 스냅샷이다. 사라진 행은 폐업 확정이 아니라 sourceStatus=missing
으로 보류하고 파일·attrs·ID를 남긴다. 새 ID는 먼저 registry에 예약해 중간 실패
후에도 재사용되지 않게 한다. 파일별 원자 교체이며 폴더 전체의 트랜잭션은 아니다.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import re
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .places import DATA_DIR, PLACES_DIR, _check_rules, _validator

DEFAULT_SOURCE = DATA_DIR / "survey" / "selected_candidates.csv"
DEFAULT_REGISTRY = DATA_DIR / "place_ids.json"
_PID = re.compile(r"p_\d{3,4}\Z")
_REQUIRED_COLUMNS = {"sourceId", "name", "category", "address", "lat", "lng"}


class ImportPlacesError(ValueError):
    """원본이나 기존 조사를 고치고 다시 시도할 수 있는 가져오기 오류."""


@dataclass(frozen=True)
class ImportReport:
    candidates: int
    total_places: int
    created: int
    updated: int
    unchanged: int
    missing: int
    marked_missing: int
    restored: int
    attrs_preserved: int
    team_preserved: int
    registry_changed: bool
    dry_run: bool

    def to_dict(self) -> dict:
        values = asdict(self)
        names = {"total_places": "totalPlaces", "marked_missing": "markedMissing",
                 "attrs_preserved": "attrsPreserved", "team_preserved": "teamPreserved",
                 "registry_changed": "registryChanged", "dry_run": "dryRun"}
        return {names.get(key, key): value for key, value in values.items()}


def _reject_constant(value: str):
    raise ImportPlacesError(f"JSON에 유한하지 않은 숫자가 있습니다: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportPlacesError(f"JSON 필드가 중복되었습니다: {key}")
        result[key] = value
    return result


def _json(raw: bytes, path: Path):
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImportPlacesError(f"{path.name}: 올바른 UTF-8 JSON이 아닙니다.") from exc


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read_optional(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _check_place(value: dict, path: Path, validator, today: date) -> None:
    errors = sorted(validator.iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise ImportPlacesError(f"{path.name}: {errors[0].json_path}: {errors[0].message}")
    problems = [i.message for i in _check_rules(value, path.name, False, today) if i.level == "error"]
    if problems:
        raise ImportPlacesError(f"{path.name}: " + "; ".join(problems))


def _read_candidates(raw: bytes, validator, today: date) -> dict[str, dict]:
    try:
        stream = io.StringIO(raw.decode("utf-8-sig"), newline="")
    except UnicodeError as exc:
        raise ImportPlacesError("후보 CSV는 UTF-8 형식이어야 합니다.") from exc
    reader = csv.DictReader(stream, strict=True)
    try:
        headers = reader.fieldnames or []
    except csv.Error as exc:
        raise ImportPlacesError(f"후보 CSV 헤더를 읽을 수 없습니다: {exc}") from exc
    if len(headers) != len(set(headers)):
        raise ImportPlacesError("후보 CSV의 열 이름이 중복되었습니다.")
    missing = _REQUIRED_COLUMNS - set(headers)
    if missing:
        raise ImportPlacesError("후보 CSV 필수 열이 없습니다: " + ", ".join(sorted(missing)))
    candidates = {}
    try:
        for row in reader:
            line = reader.line_num
            if None in row or any(row.get(key) is None for key in _REQUIRED_COLUMNS):
                raise ImportPlacesError(f"CSV {line}행: 열 개수가 헤더와 다릅니다.")
            sid = row["sourceId"].strip()
            if not sid:
                raise ImportPlacesError(f"CSV {line}행: sourceId가 비어 있습니다.")
            if sid in candidates:
                raise ImportPlacesError(f"CSV {line}행: sourceId가 중복되었습니다: {sid}")
            try:
                lat, lng = float(row["lat"]), float(row["lng"])
            except (ValueError, TypeError) as exc:
                raise ImportPlacesError(f"CSV {line}행: lat/lng가 숫자가 아닙니다.") from exc
            if not math.isfinite(lat) or not math.isfinite(lng):
                raise ImportPlacesError(f"CSV {line}행: lat/lng는 유한한 숫자여야 합니다.")
            value = {"version": 1, "id": "p_0001", "name": row["name"].strip(),
                     "category": row["category"].strip(), "lat": lat, "lng": lng,
                     "address": row["address"].strip(), "source": "semas", "sourceId": sid,
                     "sourceStatus": "active"}
            _check_place(value, Path("p_0001.json"), validator, today)
            candidates[sid] = value
    except csv.Error as exc:
        raise ImportPlacesError(f"CSV {reader.line_num}행을 읽을 수 없습니다: {exc}") from exc
    if not candidates:
        raise ImportPlacesError("후보 CSV가 비어 있습니다. 전체 가게를 missing으로 처리하지 않고 중단합니다.")
    return candidates


def _registry(raw: bytes | None, path: Path) -> dict:
    if raw is None:
        return {"version": 1, "nextId": 1, "sourceIds": {}, "reservedIds": []}
    value = _json(raw, path)
    fields = {"version", "nextId", "sourceIds", "reservedIds"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int
            or value["version"] != 1 or type(value["nextId"]) is not int
            or not 1 <= value["nextId"] <= 10000 or not isinstance(value["sourceIds"], dict)
            or not isinstance(value["reservedIds"], list)):
        raise ImportPlacesError("ID registry 형식이 잘못되었습니다.")
    ids = value["reservedIds"]
    if any(not isinstance(pid, str) or not _PID.fullmatch(pid) for pid in ids) or len(ids) != len(set(ids)):
        raise ImportPlacesError("registry reservedIds 형식 또는 중복을 확인하세요.")
    mapping = value["sourceIds"]
    if any(not isinstance(sid, str) or not sid.strip() or sid != sid.strip()
           or not isinstance(pid, str) or not _PID.fullmatch(pid) for sid, pid in mapping.items()):
        raise ImportPlacesError("registry sourceIds 형식이 잘못되었습니다.")
    if len(mapping.values()) != len(set(mapping.values())):
        raise ImportPlacesError("registry에서 여러 sourceId가 같은 식당 ID를 가리킵니다.")
    if not set(mapping.values()).issubset(ids):
        raise ImportPlacesError("registry sourceIds의 식당 ID가 reservedIds에 없습니다.")
    if value["nextId"] <= max((int(pid[2:]) for pid in ids), default=0):
        raise ImportPlacesError("registry nextId가 예약된 ID보다 작거나 같습니다.")
    return value


@dataclass
class _Plan:
    source: Path
    places: Path
    registry: Path
    snapshots: dict[Path, bytes | None]
    existing_paths: set[Path]
    writes: dict[Path, bytes]
    registry_bytes: bytes
    report: ImportReport


def _plan(source: Path, places: Path, registry: Path, dry_run: bool, today: date) -> _Plan:
    if registry.is_relative_to(places) or source == registry or source.is_relative_to(places):
        raise ImportPlacesError("CSV와 ID registry는 places 폴더 밖의 서로 다른 경로에 두세요.")
    if places.name == "samples":
        raise ImportPlacesError("실제 가게를 samples 폴더에 쓸 수 없습니다.")
    source_raw = source.read_bytes()
    validator = _validator()
    candidates = _read_candidates(source_raw, validator, today)
    registry_raw = _read_optional(registry)
    saved = _registry(registry_raw, registry)
    mapping = dict(saved["sourceIds"])
    reserved = set(saved["reservedIds"])
    owners = {pid: sid for sid, pid in mapping.items()}
    snapshots = {source: source_raw, registry: registry_raw}
    existing: dict[str, dict] = {}
    by_source: dict[str, str] = {}
    paths = set(places.glob("*.json"))
    for path in sorted(paths):
        raw = path.read_bytes()
        snapshots[path] = raw
        value = _json(raw, path)
        _check_place(value, path, validator, today)
        pid = value["id"]
        if pid in existing:
            raise ImportPlacesError(f"기존 places에 식당 ID가 중복되었습니다: {pid}")
        existing[pid] = value
        reserved.add(pid)
        if value["source"] != "semas":
            if pid in owners:
                raise ImportPlacesError(f"registry의 {pid}가 수동 추가 가게 파일과 충돌합니다.")
            continue
        sid = value["sourceId"]
        if sid != sid.strip() or not sid.strip():
            raise ImportPlacesError(f"{path.name}: sourceId 앞뒤 공백 또는 빈 값을 확인하세요.")
        if sid in by_source:
            raise ImportPlacesError(f"기존 places에 sourceId가 중복되었습니다: {sid}")
        if (sid in mapping and mapping[sid] != pid) or (pid in owners and owners[pid] != sid):
            raise ImportPlacesError(f"registry와 {path.name}의 sourceId/ID가 충돌합니다.")
        mapping[sid], owners[pid], by_source[sid] = pid, sid, pid

    next_id = max(saved["nextId"], max((int(pid[2:]) for pid in reserved), default=0) + 1)
    for sid in sorted(candidates):
        if sid in mapping:
            continue
        if next_id > 9999:
            raise ImportPlacesError("p_XXXX 식당 ID가 9999개를 초과했습니다. 스키마를 확장한 뒤 다시 실행하세요.")
        pid = f"p_{next_id:04d}"
        mapping[sid] = pid
        reserved.add(pid)
        next_id += 1

    output = {pid: copy.deepcopy(value) for pid, value in existing.items()}
    restored = 0
    for sid, candidate in candidates.items():
        pid = mapping[sid]
        old = existing.get(pid, {})
        restored += old.get("sourceStatus") == "missing"
        # attrs를 비롯한 조사 필드는 이 사전에 들어 있지 않으므로 절대 덮어쓰지 않는다.
        value = copy.deepcopy(old)
        value.update({**candidate, "id": pid})
        output[pid] = value
    marked_missing = 0
    for sid, pid in by_source.items():
        if sid not in candidates:
            marked_missing += output[pid].get("sourceStatus") != "missing"
            output[pid]["sourceStatus"] = "missing"

    writes = {}
    created = updated = unchanged = 0
    for pid, value in sorted(output.items()):
        path = places / f"{pid}.json"
        _check_place(value, path, validator, today)
        snapshots.setdefault(path, None)
        if value["source"] != "semas":
            continue  # 팀이 직접 만든 파일은 바이트/mtime까지 그대로 둔다.
        if pid not in existing:
            created += 1
        elif value != existing[pid]:
            updated += 1
        else:
            unchanged += 1
            continue
        writes[path] = _encoded(value)

    new_registry = {"version": 1, "nextId": next_id, "sourceIds": dict(sorted(mapping.items())),
                    "reservedIds": sorted(reserved, key=lambda p: (int(p[2:]), p))}
    registry_bytes = registry_raw if new_registry == saved and registry_raw is not None else _encoded(new_registry)
    report = ImportReport(
        candidates=len(candidates), total_places=len(output), created=created, updated=updated,
        unchanged=unchanged, missing=sum(v.get("sourceStatus") == "missing" for v in output.values()),
        marked_missing=marked_missing, restored=restored,
        attrs_preserved=sum("attrs" in v for v in existing.values()),
        team_preserved=sum(v["source"] == "team" for v in existing.values()),
        registry_changed=registry_bytes != registry_raw, dry_run=dry_run,
    )
    return _Plan(source, places, registry, snapshots, paths, writes, registry_bytes, report)


def _assert_unchanged(path: Path, expected: bytes | None) -> None:
    if _read_optional(path) != expected:
        raise ImportPlacesError(f"작업 중 파일이 바뀌었습니다. 덮어쓰지 않고 중단합니다: {path.name}")


def _verify_snapshot(plan: _Plan) -> None:
    if set(plan.places.glob("*.json")) != plan.existing_paths:
        raise ImportPlacesError("작업 중 places 파일 목록이 바뀌었습니다. 다시 실행하세요.")
    for path, raw in plan.snapshots.items():
        _assert_unchanged(path, raw)


@contextmanager
def _lock(path: Path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ImportPlacesError(f"다른 가져오기가 진행 중이거나 잠금이 남았습니다: {path.name}. 실행 중인 작업을 먼저 확인하세요.") from exc
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _atomic_write(path: Path, raw: bytes) -> None:
    """한 파일만 원자 교체한다. 실패/중단 후에는 같은 CSV로 재실행할 수 있다."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def import_places(source_path: str | Path = DEFAULT_SOURCE, places_dir: str | Path = PLACES_DIR,
                  registry_path: str | Path = DEFAULT_REGISTRY, *, dry_run: bool = False,
                  today: date | None = None) -> ImportReport:
    """선정 목록의 전체 스냅샷을 검증 후 가져온다. dry_run은 파일/폴더/잠금을 만들지 않는다.

    ID 예약을 먼저 저장해 이후 쓰기가 중간에 실패해도 같은 sourceId의 ID가 유지된다.
    attrs 편집과 동시에 실행하지 않는 것이 권장된다. 변경 감지는 쓰기 직전까지
    수행하지만 외부 편집기가 따르는 범용 파일 잠금 규약까지 강제하지는 않는다.
    """
    source, places, registry = (Path(p).resolve() for p in (source_path, places_dir, registry_path))
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    plan = _plan(source, places, registry, dry_run, today)
    if dry_run:
        return plan.report
    places.parent.mkdir(parents=True, exist_ok=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    # 같은 places에 다른 registry를 쓰거나 같은 registry를 다른 places에서 쓰는
    # 경우에도 ID 할당이 경쟁하지 않도록 두 자원을 모두 잠근다.
    lock_paths = {places.parent / f".{places.name}.import.lock",
                  registry.parent / f".{registry.name}.import.lock"}
    with ExitStack() as stack:
        for lock_path in sorted(lock_paths):
            stack.enter_context(_lock(lock_path))
        _verify_snapshot(plan)
        places.mkdir(exist_ok=True)
        # 예약부터 커밋한다. 이미 예약한 ID는 삭제된 파일에도 재사용하지 않는다.
        if plan.report.registry_changed:
            _assert_unchanged(registry, plan.snapshots[registry])
            _atomic_write(registry, plan.registry_bytes)
        for path, raw in plan.writes.items():
            _assert_unchanged(path, plan.snapshots[path])
            _atomic_write(path, raw)
    return plan.report
