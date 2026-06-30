import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.base import SessionLocal
from app.db.init_db import init_db
from app.delivery.emailer import send_split_telegram_digests


if __name__ == "__main__":
    init_db()
    is_daily = "--weekly" not in sys.argv
    with SessionLocal() as session:
        results = send_split_telegram_digests(session, daily=is_daily)
        for result in results:
            print(result)

