"""The built frontend served by the API process, as in the container image."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloudops.main import create_app

from .conftest import make_settings, operator_headers

INDEX = "<!doctype html><html><body><co-root></co-root></body></html>"


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    root = tmp_path / "browser"
    (root / "media").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "main-35JR6M3P.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "media" / "plex-sans-ABCD1234.woff2").write_bytes(b"wOF2")
    (tmp_path / "outside.txt").write_text("not part of the app", encoding="utf-8")
    return root


@pytest.fixture
def client(tmp_path: Path, static_dir: Path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path / "data", static_dir=static_dir)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_root_and_client_routes_get_the_app_shell_uncached(client: TestClient) -> None:
    for path in ("/", "/overview", "/applications/orders-api", "/logs?window=1h"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<co-root>" in response.text
        assert response.headers["Cache-Control"] == "no-cache"
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src" not in csp or "'unsafe-inline'" not in csp.split("script-src")[1]


def test_hashed_files_are_cached_for_good_with_the_right_types(client: TestClient) -> None:
    script = client.get("/main-35JR6M3P.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    font = client.get("/media/plex-sans-ABCD1234.woff2")
    assert font.headers["content-type"] == "font/woff2"
    assert "immutable" in font.headers["Cache-Control"]
    icon = client.get("/favicon.svg")
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.headers["Cache-Control"] == "no-cache"


def test_a_missing_asset_is_404_not_the_app_shell(client: TestClient) -> None:
    response = client.get("/main-MISSING0.js")
    assert response.status_code == 404
    assert "<co-root>" not in response.text


@pytest.mark.parametrize(
    "path", ["/../outside.txt", "/%2e%2e/outside.txt", "/media/../../outside.txt"]
)
def test_files_outside_the_build_directory_are_never_served(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert "not part of the app" not in response.text


def test_unknown_api_paths_stay_json_404s(client: TestClient) -> None:
    for path in ("/api", "/api/", "/api/nothing-here", "/api/applications/x/nothing"):
        response = client.get(path, headers=operator_headers())
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"


def test_the_api_still_answers_and_still_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/health/live").status_code == 200
    assert client.get("/api/applications").status_code == 401
    assert client.get("/api/applications", headers=operator_headers()).status_code == 200


def test_the_host_guard_also_protects_the_app_shell(client: TestClient) -> None:
    response = client.get("/", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_a_static_directory_without_index_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match=r"no index.html"):
        create_app(make_settings(tmp_path / "data", static_dir=empty))


def test_without_a_static_directory_nothing_is_served_outside_the_api(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path / "data"))) as test_client:
        assert test_client.get("/").status_code == 404


def test_uvicorn_messages_use_the_application_log_format(tmp_path: Path) -> None:
    server_logger = logging.getLogger("uvicorn.error")
    server_logger.addHandler(logging.StreamHandler())
    server_logger.propagate = False
    create_app(make_settings(tmp_path / "data"))
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("uvicorn.error").propagate
    assert logging.getLogger("uvicorn").handlers == []
