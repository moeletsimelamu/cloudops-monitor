"""Schema migrations (Alembic), run programmatically.

The migration scripts ship inside the package, so the same code path works in development, in
tests and in the container: ``upgrade_to_head`` at start-up (unless CLOUDOPS_AUTO_MIGRATE=false)
or ``cloudops db upgrade``. Developers create new revisions with ``alembic revision
--autogenerate -m "..."`` from the backend directory (see alembic.ini).
"""

from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text

from cloudops.db.session import create_db_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


@cache
def head_revision() -> str | None:
    """The newest migration shipped with this code (cached: it cannot change while running)."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(connection: Connection) -> str | None:
    """The revision the database is at; None for an empty database.

    Read directly rather than through Alembic's MigrationContext, which logs at INFO level on
    every call: this runs on every readiness probe.
    """
    if not inspect(connection).has_table("alembic_version"):
        return None
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def upgrade_to_head(database_path: Path) -> None:
    engine = create_db_engine(database_path, for_migrations=True)
    try:
        with engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
    finally:
        engine.dispose()


def downgrade_to(database_path: Path, revision: str) -> None:
    engine = create_db_engine(database_path, for_migrations=True)
    try:
        with engine.connect() as connection:
            command.downgrade(alembic_config(connection), revision)
    finally:
        engine.dispose()
