from pathlib import Path

from fastapi.testclient import TestClient

from cloudops import __version__
from cloudops.config import Settings
from cloudops.db.migrate import downgrade_to
from cloudops.db.session import create_db_engine
from cloudops.main import create_app
from tests.conftest import make_settings


def test_liveness(client: TestClient) -> None:
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive", "version": __version__}


def test_readiness_when_database_and_schema_are_ok(client: TestClient) -> None:
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "schema": "ok", "worker": "skipped"},
    }


def test_health_endpoints_need_no_authentication(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/health/live").status_code == 200
    assert client.get("/api/health/ready").status_code == 200


def test_not_ready_when_database_is_unavailable_but_still_alive(
    client: TestClient, tmp_path: Path
) -> None:
    app = client.app
    original = app.state.engine  # type: ignore[attr-defined]
    # A database inside a directory that does not exist cannot be opened.
    app.state.engine = create_db_engine(tmp_path / "missing" / "cloudops.db")  # type: ignore[attr-defined]
    try:
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "checks": {"database": "error", "schema": "skipped", "worker": "skipped"},
        }
        assert client.get("/api/health/live").status_code == 200
    finally:
        app.state.engine.dispose()  # type: ignore[attr-defined]
        app.state.engine = original  # type: ignore[attr-defined]


def test_not_ready_when_schema_is_behind(client: TestClient, settings: Settings) -> None:
    client.app.state.engine.dispose()  # type: ignore[attr-defined]
    downgrade_to(settings.database_path, "base")
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "schema": "error", "worker": "skipped"}


def test_ready_with_a_running_worker_and_not_ready_once_it_stops(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "data", worker_enabled=True)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"]["worker"] == "ok"

        client.portal.call(app.state.worker.stop)  # type: ignore[union-attr]
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["worker"] == "error"
        assert client.get("/api/health/live").status_code == 200
