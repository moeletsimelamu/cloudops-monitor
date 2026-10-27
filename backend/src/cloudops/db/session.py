"""Database engine and sessions."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_path: Path, *, for_migrations: bool = False) -> Engine:
    """An engine for the SQLite database at ``database_path``.

    The application engine uses the sqlite3 driver's default transaction handling: a write
    transaction starts at the first INSERT/UPDATE/DELETE, so a read followed by a write never
    has to upgrade a read snapshot (which SQLite refuses with "database is locked" instead of
    waiting). Writes still wait up to ``busy_timeout`` for each other.

    The migration engine differs in two ways, both from SQLite's guidance for schema changes:
    every migration runs inside a real BEGIN/COMMIT (DDL included), so a failed migration leaves
    no half-changed schema; and foreign keys are off, because Alembic's batch mode re-creates
    tables, which would otherwise cascade deletes into child tables.
    """
    engine = create_engine(
        f"sqlite:///{database_path}",
        # Sessions are used from FastAPI's thread pool and from the monitoring worker's threads;
        # each session still holds its own connection.
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: SQLiteConnection, _record: Any) -> None:
        if for_migrations:
            dbapi_connection.isolation_level = None  # SQLAlchemy emits BEGIN itself (below)
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA foreign_keys = {'OFF' if for_migrations else 'ON'}")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()

    if for_migrations:

        @event.listens_for(engine, "begin")
        def _begin(connection: Connection) -> None:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session that commits on success and rolls back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
