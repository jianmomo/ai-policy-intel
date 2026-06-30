import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import run_policy_refresh


if __name__ == "__main__":
    summary = run_policy_refresh()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
