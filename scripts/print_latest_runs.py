import argparse
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.base import SessionLocal
from app.db.models import RunLog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print recent AI Policy Intel run logs.")
    parser.add_argument("--limit", type=int, default=20, help="How many recent rows to print.")
    parser.add_argument("--type", dest="run_type", default="", help="Optional run type filter, such as daily or weekly.")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    with SessionLocal() as session:
        query = select(RunLog).order_by(RunLog.id.desc()).limit(max(args.limit, 1))
        rows = session.execute(query).scalars().all()
        if args.run_type:
            rows = [row for row in rows if row.run_type == args.run_type]
        for row in rows:
            print(f"{row.created_at} | {row.run_type} | {row.status} | {row.message}")
