from __future__ import annotations

import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tarfile
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.db.base import SessionLocal
from app.db.init_db import init_db
from app.db.models import RunLog


def main() -> None:
    init_db()
    timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    backup_name = f'ai-policy-intel-backup-{timestamp}.tar.gz'
    backup_path = settings.backup_dir / backup_name
    paths = [
        settings.data_dir / 'app.db',
        settings.config_dir,
        settings.digest_dir,
        Path('.env'),
    ]

    with tarfile.open(backup_path, 'w:gz') as archive:
        for path in paths:
            if path.exists():
                archive.add(path, arcname=path.as_posix())

    backups = sorted(settings.backup_dir.glob('*.tar.gz'), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in backups[settings.backup_keep_count :]:
        stale.unlink(missing_ok=True)

    with SessionLocal() as session:
        session.add(RunLog(run_type='backup', status='success', message=f'created {backup_name}'))
        session.commit()

    print(backup_path)


if __name__ == '__main__':
    main()
