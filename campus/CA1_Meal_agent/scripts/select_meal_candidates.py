"""실행: python scripts/select_meal_candidates.py --limit 100 [--dry-run]."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meal.selection import main

if __name__ == "__main__":
    raise SystemExit(main())
