"""공공 후보 원본에서 밥집만 정해진 개수로 선정한다. 원본·조사 attrs는 수정하지 않는다."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .places import ROOT, _validator

DEFAULT_SOURCE = ROOT / "data" / "survey" / "candidates.csv"
DEFAULT_OUTPUT = ROOT / "data" / "survey" / "selected_candidates.csv"
DEFAULT_REPORT = ROOT / "data" / "survey" / "selection_report.json"

# 실제 음식 업종명만 허용한다. 모르는 소분류를 임의로 한식에 넣지 않는다.
MEAL_SUBCATEGORIES = frozenset({
    "백반/한정식", "국/탕/찌개류", "국수/칼국수", "냉면/밀면", "전/부침개", "기타 한식 음식점",
    "돼지고기 구이/찜", "소고기 구이/찜", "닭/오리고기 구이/찜", "곱창 전골/구이", "족발/보쌈",
    "해산물 구이/찜", "김밥/만두/분식", "버거", "토스트/샌드위치/샐러드", "치킨", "피자",
    "그 외 기타 간이 음식점", "중국집", "마라탕/훠궈", "일식 회/초밥", "횟집", "일식 카레/돈가스/덮밥",
    "일식 면 요리", "경양식", "파스타/스테이크", "기타 서양식 음식점", "베트남식 전문", "기타 동남아식 전문",
    "구내식당", "뷔페",
})

# 외부 확인이나 팀 검토로 제외할 sourceId를 명시한다. 이름이 모호하다는 이유만으로
# 술집·카페라고 단정하지 않는다. 추가할 때 근거와 이유를 함께 기록한다.
SOURCE_ID_EXCLUSIONS: dict[str, str] = {
    "MA0101202601A0036831": "야작야:작: 같은 주소의 야작이 술집으로 확인되어 제외. https://www.diningcode.com/profile.php?rid=67JjNxa9lBll",
    "MA010120220813782178": "스위티움: 법인명으로 편의점 등 여러 업종이 혼재해 식당 실체 확인 전 보류. 카페 확정 아님. https://www.dongnealba.com/job-ad/66d261b5bfa499aa424f9ef9",
    "MA0106202201A2261921": "스위티움: 법인명으로 편의점 등 여러 업종이 혼재해 식당 실체 확인 전 보류. 카페 확정 아님. https://www.dongnealba.com/job-ad/66d261b5bfa499aa424f9ef9",
    "MA0101202307A0061296": "버블베어: 식사가 가능한 업소인지 확인되지 않아 조사 전 보류. 카페 확정 아님.",
    "MA0101202406A0114177": "미나미: 우치로104-3의 이자카야로 확인되어 제외. https://www.diningcode.com/profile.php?rid=j20zmIWM6MAt",
    "MA010120220809928033": "한경민써브웨이광주전남대 후문점: 우치로124 1층의 MA0101202502A0037060과 같은 매장의 이름 변형으로 선정만 제외. sourceId를 병합하지 않음.",
    "MA0106202401A0009432": "한국맥도날드전남DT점: 우치로134의 MA0106202401A0008612와 같은 매장의 이름 변형으로 선정만 제외. sourceId를 병합하지 않음.",
    "MA0101202607A0012667": "모어레스 전대점: 우치로118의 커피숍으로 확인되어 제외. https://dining.ayo.pe.kr/cafe/28b3859a3e5aca647687fbdd32b381ee",
    "MA0101202603A0047681": "대설 전남대점: 우치로92의 빙수 전문점으로 확인되어 제외. https://daesul.com/ ; https://www.diningcode.com/profile.php?rid=Qyp5PYurqcSW",
    "MA0101202602A0030500": "이슬로: 자미로67 2층의 요리주점으로 확인되어 제외. https://www.diningcode.com/list.dc?query=%EC%A0%84%EB%8C%80%EC%A0%95%EB%AC%B8+%ED%81%AC%EB%A6%AC%EC%8A%A4%EB%A7%88%EC%8A%A4",
    "MA010120220808446744": "그냥전집: 용주로30번길70에서 식사형태로 이용 가능한지 확인할 근거가 부족하여 조사 전 보류. 술집 확정 아님.",
    "MA0101202406A0266389": "골목: 밥집·술집 겸업으로 확인되어 술집 제외 요청에 따라 선정 보류. https://www.diningcode.com/rank/%EC%A0%84%EB%8C%80%EC%A0%95%EB%AC%B8-%EC%A0%80%EB%A0%B4%ED%95%9C%EA%B0%80%EA%B2%A9",
}

# '뉴요커피자'의 '커피', '광주점'의 '주점', '바로김밥'의 '바'를 오인하지 않는다.
NAME_EXCLUSIONS = (
    ("name_cafe", re.compile(r"카페|커피(?!자)|coffee|caf[eé](?![a-z])", re.I)),
    ("name_convenience", re.compile(
        r"편의점|세븐일레븐|이마트\s*24|미니스톱|씨유|(?<![a-z0-9])cu(?![a-z])|(?<![a-z0-9])gs\s*25(?!\d)", re.I)),
    ("name_bar", re.compile(
        r"호프|포장마차|포차|술집|맥주|와인바|막걸리|이자카야|칵테일|(?:요리|민속|전통|퓨전|실내)주점"
        r"|(?:^|[\s·()])주점(?:$|[\s·()])|(?<![a-z])(?:pub|beer|wine\s*bar)(?![a-z])", re.I)),
)
REASON_LABELS = {"subcategory": "식사업종 허용 목록 밖", "name_cafe": "카페·커피 명칭",
                 "name_convenience": "편의점 명칭", "name_bar": "술집 명칭",
                 "review_exclusion": "개별 확인에 따른 제외", "duplicate_store": "같은 상호·주소·층 중복",
                 "outside_limit": "선정 개수 밖"}
REQUIRED_FIELDS = {"sourceId", "name", "category", "subCategory", "boundaryM", "lat", "lng", "address"}


@dataclass
class SelectionResult:
    rows: list[dict[str, str]]
    fieldnames: list[str]
    report: dict


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _load_rows(path: Path) -> tuple[list[dict[str, str]], list[str], str]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or not REQUIRED_FIELDS.issubset(fields):
        raise ValueError("후보 CSV의 필수 열 또는 중복 열을 확인하세요.")
    rows = list(reader)
    if not rows:
        raise ValueError("후보 CSV가 비어 있습니다.")
    seen: set[str] = set()
    validator = _validator()
    for line, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"후보 CSV {line}행의 열 개수가 맞지 않습니다.")
        source_id = row["sourceId"]
        if not source_id.strip() or source_id != source_id.strip() or source_id in seen:
            raise ValueError(f"후보 CSV {line}행의 sourceId가 비었거나 중복·공백이 있습니다.")
        seen.add(source_id)
        try:
            lat, lng, distance = (float(row[key]) for key in ("lat", "lng", "boundaryM"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"후보 CSV {line}행의 좌표·경계 거리가 숫자가 아닙니다.") from exc
        if not all(math.isfinite(v) for v in (lat, lng, distance)) or distance < 0:
            raise ValueError(f"후보 CSV {line}행의 좌표·경계 거리는 유한해야 하며 거리는 0 이상이어야 합니다.")
        place = {"version": 1, "id": "p_0001", "source": "semas", "sourceId": source_id,
                 "name": row["name"], "category": row["category"], "lat": lat, "lng": lng, "address": row["address"]}
        errors = sorted(validator.iter_errors(place), key=lambda error: error.json_path)
        if errors or not row["name"].strip() or not row["subCategory"].strip():
            message = errors[0].message if errors else "이름·소분류가 비었습니다"
            raise ValueError(f"후보 CSV {line}행이 places 형식에 맞지 않습니다: {message}")
    return rows, fields, hashlib.sha256(raw).hexdigest()


def exclusion_reason(row: dict[str, str], source_id_exclusions: dict[str, str] | None = None) -> str | None:
    overrides = SOURCE_ID_EXCLUSIONS if source_id_exclusions is None else source_id_exclusions
    if row["sourceId"] in overrides:
        return "review_exclusion"
    if row["category"] == "카페·베이커리" or row["subCategory"] not in MEAL_SUBCATEGORIES:
        return "subcategory"
    name = unicodedata.normalize("NFKC", row["name"])
    for reason, pattern in NAME_EXCLUSIONS:
        if pattern.search(name):
            return reason
    return None


def select_candidates(source_path: Path | str = DEFAULT_SOURCE, *, limit: int = 100,
                      source_id_exclusions: dict[str, str] | None = None) -> SelectionResult:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("선정 개수 limit은 양의 정수여야 합니다.")
    path = Path(source_path)
    rows, fields, source_hash = _load_rows(path)
    overrides = SOURCE_ID_EXCLUSIONS if source_id_exclusions is None else source_id_exclusions
    excluded = []
    eligible = []
    for row in rows:
        reason = exclusion_reason(row, overrides)
        if reason:
            item = {"sourceId": row["sourceId"], "name": row["name"], "reason": reason}
            if reason == "review_exclusion":
                item["detail"] = overrides[row["sourceId"]]
            excluded.append(item)
        else:
            eligible.append(row)

    # 원천 sourceId를 합치지 않고 이번 선정에서만 중복 업소를 한 곳으로 센다.
    # 같은 건물의 다른 식당·층은 보존한다. 주소가 없으면 이름만으로 합치지 않는다.
    groups: dict[tuple, list[dict[str, str]]] = {}
    for row in eligible:
        key = (_normalized(row["name"]), _normalized(row["address"]), _normalized(row.get("floor", "")))
        if not key[1]:
            key = (row["sourceId"],)
        groups.setdefault(key, []).append(row)
    distinct = []
    for group in groups.values():
        group.sort(key=lambda row: (row.get("licensed") != "주소+이름", row["sourceId"]))
        keep = group[0]
        distinct.append(keep)
        excluded.extend({"sourceId": row["sourceId"], "name": row["name"],
                         "reason": "duplicate_store", "keptSourceId": keep["sourceId"]} for row in group[1:])
    distinct.sort(key=lambda row: (float(row["boundaryM"]), row["sourceId"]))
    picked = distinct[:limit]
    excluded.extend({"sourceId": row["sourceId"], "name": row["name"], "reason": "outside_limit"}
                    for row in distinct[limit:])
    excluded.sort(key=lambda row: row["sourceId"])
    counts = Counter(row["reason"] for row in excluded)
    report = {
        "version": 1, "sourceFile": path.name, "sourceSha256": source_hash,
        "sourceCount": len(rows), "limit": limit, "selectedCount": len(picked),
        "eligibleBeforeDedup": len(eligible), "eligibleDistinct": len(distinct),
        "criteria": {
            "mealSubcategories": sorted(MEAL_SUBCATEGORIES),
            "excludedCategories": ["카페·베이커리"],
            "namePatterns": {reason: pattern.pattern for reason, pattern in NAME_EXCLUSIONS},
            "sourceIdExclusions": dict(sorted(overrides.items())),
            "dedup": "NFKC·공백제거 상호+주소+층이 같을 때 주소+이름 인허가 우선, sourceId 순으로 하나 선정. 원천 ID는 병합하지 않음",
            "order": "boundaryM 오름차순, sourceId 오름차순",
            "distanceNote": "캠퍼스 경계까지 직선거리 기준이며 실제 강의실에서의 OSM 도보 시간이 아님",
            "surveyNote": "메뉴·영업시간 등 attrs는 생성하지 않음. 실제 영업 여부는 현장 확인 필요",
        },
        "excludedCounts": dict(sorted(counts.items())),
        "reasonLabels": REASON_LABELS,
        "selected": [{"sourceId": row["sourceId"], "name": row["name"], "category": row["category"],
                      "subCategory": row["subCategory"], "boundaryM": float(row["boundaryM"])} for row in picked],
        "excluded": excluded,
    }
    return SelectionResult(picked, fields, report)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    descriptor, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_selection(selection: SelectionResult, *, source_path: Path | str = DEFAULT_SOURCE,
                    output_path: Path | str = DEFAULT_OUTPUT, report_path: Path | str = DEFAULT_REPORT) -> None:
    source, output, report = map(Path, (source_path, output_path, report_path))
    if len({path.resolve() for path in (source, output, report)}) != 3:
        raise ValueError("원본 CSV·선정 CSV·보고서 경로는 서로 달라야 합니다.")
    if hashlib.sha256(source.read_bytes()).hexdigest() != selection.report["sourceSha256"]:
        raise ValueError("선정 중 원본 CSV가 바뀌었습니다. 다시 실행하세요.")
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=selection.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(selection.rows)
    report_bytes = (json.dumps(selection.report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(output, text.getvalue().encode("utf-8-sig"))
    _atomic_write(report, report_bytes)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        selection = select_candidates(args.source, limit=args.limit)
        if not args.dry_run:
            write_selection(selection, source_path=args.source, output_path=args.output, report_path=args.report)
    except (OSError, ValueError) as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    report = selection.report
    print(f"{'[미리보기] ' if args.dry_run else ''}원본 {report['sourceCount']}곳 → 식사업종·중복 제외 후 {report['eligibleDistinct']}곳 → 선정 {report['selectedCount']}곳")
    for reason, count in report["excludedCounts"].items():
        print(f"  {REASON_LABELS[reason]}: {count}곳")
    for index, row in enumerate(report["selected"], 1):
        print(f"{index:>3}. {row['sourceId']}  {row['name']}  [{row['subCategory']}]  경계 {row['boundaryM']:g}m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
