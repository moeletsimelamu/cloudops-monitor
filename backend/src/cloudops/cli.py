"""Command-line tasks: ``cloudops db upgrade``, ``cloudops db backup PATH`` and
``cloudops generate-token``. Settings come from the same CLOUDOPS_* variables as the server."""

import argparse
import secrets
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from cloudops.config import Settings
from cloudops.db.migrate import upgrade_to_head


def backup_database(source: Path, destination: Path) -> None:
    """Copy the database with SQLite's online backup API: safe while the server is running."""
    if not source.exists():
        raise FileNotFoundError(f"No database at {source}.")
    if destination.exists():
        raise FileExistsError(f"{destination} already exists; choose a new file name.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # closing(): a sqlite3 connection's own context manager commits but does not close.
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloudops", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    db = commands.add_parser("db", help="database tasks").add_subparsers(dest="db_command")
    db.required = True
    db.add_parser("upgrade", help="apply pending schema migrations")
    backup = db.add_parser("backup", help="write a consistent copy of the database")
    backup.add_argument("path", type=Path)
    commands.add_parser("generate-token", help="print a random operator token")
    args = parser.parse_args(argv)

    if args.command == "generate-token":
        print(secrets.token_urlsafe(32))
        return 0

    settings = Settings()
    if args.db_command == "upgrade":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        upgrade_to_head(settings.database_path)
        print(f"Database at {settings.database_path} is up to date.")
        return 0
    try:
        backup_database(settings.database_path, args.path)
    except (FileNotFoundError, FileExistsError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Backed up {settings.database_path} to {args.path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
