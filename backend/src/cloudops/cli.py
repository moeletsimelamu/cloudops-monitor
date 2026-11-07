"""Command-line tasks: ``cloudops serve``, ``cloudops db upgrade``, ``cloudops db backup PATH`` and
``cloudops generate-token``. Settings come from the same CLOUDOPS_* variables as the server."""

import argparse
import logging
import os
import secrets
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from cloudops.config import ConfigurationError, load_settings
from cloudops.db.migrate import upgrade_to_head
from cloudops.logging_setup import configure_logging

logger = logging.getLogger("cloudops.cli")

# Exit status when the configuration is invalid (uvicorn uses 3 for other start-up failures).
EXIT_INVALID_CONFIGURATION = 2
# Exit status when the data directory or database cannot be used (as uvicorn's start-up failure).
EXIT_STORAGE_UNUSABLE = 3


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


def report_invalid_configuration(error: ConfigurationError) -> None:
    """One log record in the configured format (so a container's output stays one JSON object
    per line), listing every problem. No traceback: the problems are the operator's to fix, not
    a bug. Secret values never appear in the messages; any set in the environment are masked
    anyway."""
    log_format = os.environ.get("CLOUDOPS_LOG_FORMAT", "json").strip().lower()
    secrets_in_env = [
        value
        for name in ("CLOUDOPS_OPERATOR_TOKEN", "CLOUDOPS_GITHUB_TOKEN")
        if (value := os.environ.get(name, "").strip())
    ]
    configure_logging("INFO", "text" if log_format == "text" else "json", secrets_in_env)
    logger.critical(
        "CloudOps Monitor cannot start: the configuration is invalid. %s",
        " ".join(error.errors),
        extra={"fields": {"event": "config.invalid", "errors": error.errors}},
    )


def serve(host: str, port: int) -> int:
    """Run the server. Unlike ``uvicorn --factory``, an invalid configuration ends with a readable
    message and exit status 2 instead of a traceback."""
    import uvicorn

    from cloudops.main import (
        StorageError,
        create_app,
        prepare_storage,
    )

    try:
        app = create_app()
    except ConfigurationError as error:
        report_invalid_configuration(error)
        return EXIT_INVALID_CONFIGURATION
    try:
        # Before the server starts, so an unusable database is one clear record, not a
        # traceback from the start-up hook. Start-up runs it again; that is a no-op by then.
        prepare_storage(app.state.settings)
    except StorageError:
        return EXIT_STORAGE_UNUSABLE
    # log_config=None: create_app has already routed uvicorn's loggers through the application's
    # handler. The application writes its own access log (path only).
    # proxy_headers=False: uvicorn would otherwise believe X-Forwarded-* from 127.0.0.1, so any
    # local process could pose as another client. The app applies CLOUDOPS_TRUSTED_PROXIES itself.
    uvicorn.run(
        app,
        host=host,
        port=port,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=15,
        proxy_headers=False,
        server_header=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloudops", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="run the server")
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="interface to listen on (default: 127.0.0.1)"
    )
    serve_parser.add_argument("--port", type=int, default=8300, help="port (default: 8300)")
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

    if args.command == "serve":
        return serve(args.host, args.port)

    try:
        settings = load_settings()
    except ConfigurationError as error:
        report_invalid_configuration(error)
        return EXIT_INVALID_CONFIGURATION
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
