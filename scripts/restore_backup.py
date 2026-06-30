from __future__ import annotations

import sys
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import shutil
import sys
import tarfile
from pathlib import Path

from app.config import settings


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: python scripts/restore_backup.py <backup.tar.gz>')
    backup_path = Path(sys.argv[1]).expanduser().resolve()
    if not backup_path.exists():
        raise SystemExit(f'backup not found: {backup_path}')

    restore_dir = settings.data_dir / 'restore-temp'
    if restore_dir.exists():
        shutil.rmtree(restore_dir)
    restore_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, 'r:gz') as archive:
        archive.extractall(restore_dir)

    print(f'extracted to {restore_dir}')
    print('review extracted files, then copy the desired app.db/configs/.env back into place manually')


if __name__ == '__main__':
    main()
