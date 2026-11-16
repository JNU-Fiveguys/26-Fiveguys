"""가게 데이터 검사: python -m meal.check_data

data/settings.json 과 data/places/ · data/samples/ 를 스키마 + 규칙으로 검사한다.
오류가 하나라도 있으면 종료 코드 1 (나중에 CI 에 붙일 수 있다).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .places import check_dirs, price_band, price_label, rep_price, recommendation_ready
from .settings import load_settings


def _warning_kind(message: str) -> str:
    for prefix, label in (("attrs 없음", "attrs 미조사"), ("attrs 부분 조사", "attrs 부분 조사·보완 필요"), ("캠퍼스 중심", "캠퍼스 중심에서 먼 좌표"),
                          ("검증한 지", "조사 후 90일 경과"), ("후보 목록에서 빠짐", "후보 목록 누락·재확인 필요")):
        if message.startswith(prefix):
            return label
    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true", help="개별 가게 대신 합계와 경고 종류별 개수만 출력")
    args = parser.parse_args(argv if argv is not None else [])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings_error = None
    try:
        load_settings()
    except (ValueError, OSError) as error:
        settings_error = str(error)
        print(f"[오류] settings.json: {settings_error}")

    result = check_dirs()

    if not args.summary:
        print(f"{'id':<7}{'종류':<9}{'유형':<7}{'대표가':>7}  {'가격대':<6}{'칩':<9}이름")
        print("-" * 72)
        for p in result.places:
            attrs = p.get("attrs")
            price = rep_price(p)
            print(
                f"{p['id']:<7}{p['category']:<9}{(attrs or {}).get('mealType', '-'):<7}"
                f"{price if price is not None else '-':>7}  {price_band(price) or '-':<6}"
                f"{price_label(price) or '정보 없음':<9}{p['name']}"
            )

    if result.issues:
        print()
        for issue in result.issues:
            if not args.summary or issue.level == "error":
                print(issue)
        if args.summary:
            warnings = Counter(_warning_kind(i.message) for i in result.issues if i.level == "warn")
            for label, count in sorted(warnings.items()):
                print(f"[경고 요약] {label}: {count}건")

    n_err = len(result.errors) + (settings_error is not None)
    n_ready = sum(recommendation_ready(p) for p in result.places)
    print()
    n_warn = len(result.issues) - len(result.errors)
    print(f"가게 {len(result.places)}곳 · 추천 가능 {n_ready}곳 · 오류 {n_err} · 경고 {n_warn}")
    if args.summary:
        real = [p for p in result.places if p["source"] != "sample"]
        missing = sum(p.get("sourceStatus", "active") == "missing" for p in real)
        ready = sum(recommendation_ready(p) for p in real)
        partial = sum(p.get('attrs', {}).get('status') == 'partial' for p in real)
        print(f"실제 가게 {len(real)}곳 · 추천 대상 {ready}곳 · 부분 조사 표시 {partial}곳 · attrs 미조사 {sum(not p.get('attrs') for p in real)}곳 · 후보 누락 {missing}곳 · 샘플 {len(result.places) - len(real)}곳")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
