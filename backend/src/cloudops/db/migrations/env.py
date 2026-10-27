"""Alembic environment.

Called with a ready connection by ``cloudops.db.migrate`` (application, CLI and tests), or
stand-alone by the ``alembic`` command for authoring revisions, in which case the database path
comes from the normal CloudOps settings.
"""

from alembic import context
from sqlalchemy import Connection

from cloudops.config import Settings
from cloudops.db.models import Base
from cloudops.db.session import create_db_engine

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER most column properties; batch mode re-creates the table instead.
        render_as_batch=True,
        compare_type=True,
        # The migration engine gives SQLite real BEGIN/COMMIT around DDL (see
        # cloudops.db.session), so the whole upgrade is one transaction: all or nothing.
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(settings.database_path, for_migrations=True)
    try:
        with engine.connect() as new_connection:
            _run(new_connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not supported; run them online.")
run_migrations_online()
