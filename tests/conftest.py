import os
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DATABASE = Path(tempfile.gettempdir()) / f"ai-policy-intel-pytest-{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"

from app.db.base import Base, engine  # noqa: E402
import app.db.models  # noqa: E402,F401


@pytest.fixture(autouse=True)
def isolated_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    engine.dispose()
    TEST_DATABASE.unlink(missing_ok=True)
