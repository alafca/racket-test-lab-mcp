import pytest

from racket_lab import db
from racket_lab.seed import seed


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A freshly seeded temporary database, also exposed through RACKET_LAB_DB."""
    path = tmp_path / "lab.db"
    seed(path)
    monkeypatch.setenv("RACKET_LAB_DB", str(path))
    return path


@pytest.fixture
def conn(db_path):
    with db.connect(db_path) as conn:
        yield conn
