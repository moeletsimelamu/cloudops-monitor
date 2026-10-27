from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from cloudops.db.migrate import current_revision, downgrade_to, head_revision, upgrade_to_head
from cloudops.db.models import Application, Base, HealthCheck, Incident, OperationalEvent
from cloudops.db.session import create_db_engine, create_session_factory, session_scope

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
EXPECTED_TABLES = {
    "applications",
    "health_checks",
    "incidents",
    "deployments",
    "events",
    "log_entries",
    "operator_sessions",
    "system_state",
    "alembic_version",
}


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "cloudops.db"
    upgrade_to_head(path)
    return path


@pytest.fixture
def engine(database: Path) -> Iterator[Engine]:
    engine = create_db_engine(database)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with create_session_factory(engine)() as session:
        yield session


def _application(slug: str = "orders-api") -> Application:
    return Application(
        slug=slug, name="Orders API", environment="demo", check_url="http://localhost:8310/health"
    )


def test_migrations_create_every_table_at_head(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as connection:
        assert current_revision(connection) == head_revision()


def test_migrated_schema_matches_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_downgrade_to_base_and_upgrade_again(database: Path) -> None:
    downgrade_to(database, "base")
    engine = create_db_engine(database)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
    upgrade_to_head(database)
    upgrade_to_head(database)  # idempotent
    engine = create_db_engine(database)
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_migration_engine_rolls_back_schema_changes(database: Path) -> None:
    """Schema changes made by a failed migration must not survive (all-or-nothing upgrades)."""
    engine = create_db_engine(database, for_migrations=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 0
            connection.execute(text("CREATE TABLE half_done (id INTEGER)"))
            connection.rollback()
        assert "half_done" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_connection_pragmas(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000


def test_timestamps_are_stored_as_utc_and_returned_aware(db: Session) -> None:
    app = _application()
    # 16:00 at UTC+2 is 14:00 UTC.
    app.created_at = datetime(2026, 10, 27, 16, 0, tzinfo=timezone(timedelta(hours=2)))
    db.add(app)
    db.commit()
    db.expire_all()
    stored = db.get(Application, app.id)
    assert stored is not None
    assert stored.created_at == datetime(2026, 10, 27, 14, 0, tzinfo=UTC)
    assert stored.created_at.tzinfo is UTC


def test_naive_timestamps_are_rejected(db: Session) -> None:
    app = _application()
    app.created_at = datetime(2026, 10, 27, 14, 0)  # deliberately naive
    db.add(app)
    with pytest.raises(StatementError, match="Naive datetime"):
        db.commit()


def test_check_constraints_reject_invalid_values(db: Session) -> None:
    app = _application()
    app.health_state = "sort-of-ok"
    db.add(app)
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_an_application_removes_its_records_but_keeps_events(db: Session) -> None:
    app = _application()
    db.add(app)
    db.flush()
    db.add(HealthCheck(application_id=app.id, started_at=NOW, signal="ok", state_after="healthy"))
    db.add(
        OperationalEvent(
            occurred_at=NOW,
            type="application.registered",
            severity="info",
            application_id=app.id,
            summary="Registered Orders API",
        )
    )
    db.commit()

    db.delete(app)
    db.commit()
    assert db.scalars(select(HealthCheck)).all() == []
    event = db.scalars(select(OperationalEvent)).one()
    assert event.application_id is None
    assert event.summary == "Registered Orders API"


def test_only_one_open_incident_per_application(db: Session) -> None:
    app = _application()
    db.add(app)
    db.flush()
    db.add(Incident(application_id=app.id, opened_at=NOW, closed_at=NOW, worst_state="degraded"))
    db.add(Incident(application_id=app.id, opened_at=NOW, worst_state="degraded"))
    db.commit()  # a closed incident plus one open incident is fine

    db.add(Incident(application_id=app.id, opened_at=NOW, worst_state="unavailable"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with pytest.raises(RuntimeError), session_scope(factory) as session:
        session.add(_application())
        session.flush()
        raise RuntimeError("boom")
    with session_scope(factory) as session:
        assert session.scalars(select(Application)).all() == []
