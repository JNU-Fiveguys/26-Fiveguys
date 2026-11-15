"""후보 CSV → 안정된 ID와 기존 조사 attrs를 보존하는 places JSON.

python scripts/import_places.py --dry-run
python scripts/import_places.py

CSV는 선정된 식당 목록의 전체 스냅샷이다. 빠진 가게는 삭제하지 않고 missing으로 보류한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meal.import_places import DEFAULT_REGISTRY, DEFAULT_SOURCE, ImportPlacesError, import_places
from meal.places import PLACES_DIR


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--places-dir", type=Path, default=PLACES_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--dry-run", action="store_true", help="검증과 변경 개수만 확인하고 파일을 쓰지 않음")
    args = parser.parse_args()
    try:
        result = import_places(args.source, args.places_dir, args.registry, dry_run=args.dry_run)
    except (ImportPlacesError, OSError) as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if args.dry_run:
        print("미리보기 완료: 파일을 변경하지 않았습니다.")
    else:
        print("가져오기 완료: attrs를 보존했습니다. missing은 원본 목록 누락이며 폐업 확정이 아닙니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
