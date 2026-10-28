"""Small builders shared by tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from cloudops.db.migrate import upgrade_to_head
from cloudops.db.models import Application
from cloudops.db.session import create_db_engine, create_session_factory
from cloudops.domain.classification import Observation, ProbeError


@contextmanager
def migrated_session_factory(directory: Path) -> Iterator[sessionmaker[Session]]:
    path = directory / "cloudops.db"
    upgrade_to_head(path)
    engine = create_db_engine(path)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def make_application(db: Session, slug: str = "orders-api", **overrides: Any) -> Application:
    values: dict[str, Any] = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "environment": "demo",
        "check_url": "http://localhost:8310/health",
    }
    values.update(overrides)
    app = Application(**values)
    db.add(app)
    db.flush()
    return app


def ok(duration_ms: int = 40) -> Observation:
    return Observation(
        duration_ms, status_code=200, body=b'{"status":"pass"}', resolved_ip="127.0.0.1"
    )


def http(status: int, duration_ms: int = 40) -> Observation:
    return Observation(duration_ms, status_code=status, resolved_ip="127.0.0.1")


def error(kind: ProbeError, detail: str | None = None) -> Observation:
    return Observation(5000, error=kind, error_detail=detail)
