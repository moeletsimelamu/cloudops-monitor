import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cloudops.cli import main
from cloudops.db.migrate import head_revision


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    monkeypatch.setenv("CLOUDOPS_DATA_DIR", str(directory))
    monkeypatch.chdir(tmp_path)  # so no developer .env file is read
    return directory


def _revision(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0])


def test_generate_token_prints_a_long_random_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate-token"]) == 0
    first = capsys.readouterr().out.strip()
    main(["generate-token"])
    second = capsys.readouterr().out.strip()
    assert len(first) >= 43
    assert first != second


def test_db_upgrade_creates_the_schema(data_dir: Path) -> None:
    assert main(["db", "upgrade"]) == 0
    assert _revision(data_dir / "cloudops.db") == head_revision()


def test_db_backup_copies_the_database_and_never_overwrites(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["db", "upgrade"])
    target = tmp_path / "backups" / "copy.db"
    assert main(["db", "backup", str(target)]) == 0
    assert _revision(target) == head_revision()

    assert main(["db", "backup", str(target)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_db_backup_without_a_database_fails_clearly(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["db", "backup", str(tmp_path / "copy.db")]) == 1
    assert "No database" in capsys.readouterr().err
