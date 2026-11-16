import sys
from pathlib import Path

# `pytest campus/CA1_Meal_agent` 어디서 돌려도 `import meal` 이 되게
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
