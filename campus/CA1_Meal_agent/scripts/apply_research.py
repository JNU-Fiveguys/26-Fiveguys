"""검토한 전체 웹 조사 결과를 적용한다. --dry-run으로 먼저 검사할 수 있다."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from meal.research import apply_research


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="part_*.json 조사 결과가 있는 폴더")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = apply_research(sorted(args.folder.glob("part_*.json")), dry_run=args.dry_run)
    if not args.dry_run:
        (args.folder / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        labels = {"confirmed": "웹 게시값 확인", "partial": "부분 자료", "conflict": "정보 상충", "unconfirmed": "미확인"}
        lines = [f"# 실제 식당 {report['total']}곳 공개 자료 조사", "", "확인일: " + ", ".join(report["checkedDates"]) + " (한국 시간)", "",
                 f"조사 시도 {report['researched']}곳 · 메뉴·가격 게시값 확인 {report['menusConfirmed']}곳 · 요일별 시간 게시값 확인 {report['hoursConfirmed']}곳 · 둘 다 확인 {report['bothConfirmed']}곳.", "",
                 "웹 페이지의 게시값을 확인한 결과이며 현재 가격·실제 영업을 보증하는 현장 검증은 아니다. 상충·과거 후기·동일 매장 불명확 자료는 attrs로 확정하지 않았다.", "",
                 "확인하지 못한 요일을 휴무로, 미게시 브레이크·라스트오더를 없음으로 간주하지 않는다. 좌석·혼밥 항목은 사용자 요청으로 수집·점수에서 제외했다. 포장·식사유형도 추정 저장하지 않는다.", "",
                 f"각 식당 JSON의 research에 검색어·출처·확인일·근거·unknownFields를 저장했다. 메뉴·가격과 영업시간이 모두 확인된 {report['ready']}곳은 추천 대상으로 사용하며, 부분 조사 표시는 현장 검증을 뜻하지 않는다. 실제 추천은 영업·이동·시간·예산 조건을 추가 적용한다.", "",
                 "| ID | 식당 | 메뉴·가격 | 영업시간 |", "|---|---|---|---|"]
        for p in report["places"]:
            name = p["name"].replace("|", "\\|")
            lines.append(f"| [{p['id']}](../../places/{p['id']}.json) | {name} | {labels[p['menus']]} | {labels[p['hours']]} |")
        (args.folder / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "places"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
