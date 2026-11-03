import gc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloudops.config import Settings
from cloudops.main import create_app

OPERATOR_TOKEN = "test-operator-token-0123456789-abcdefghij"


def make_settings(data_dir: Path, **overrides: Any) -> Settings:
    # _env_file=None: tests must not pick up a developer's local .env file. The test client sends
    # "Host: testserver".
    overrides.setdefault("allowed_hosts", ["localhost", "127.0.0.1", "testserver"])
    overrides.setdefault("auth_mode", "token")
    overrides.setdefault("operator_token", OPERATOR_TOKEN)
    overrides.setdefault("worker_enabled", False)
    return Settings(_env_file=None, data_dir=data_dir, **overrides)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "data")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # The context manager runs the lifespan (data directory and migrations).
    with TestClient(app) as test_client:
        yield test_client


def operator_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


@pytest.fixture(autouse=True)
def _collect_garbage() -> Iterator[None]:
    """Collect after every test, so a leaked socket or connection fails the test that leaked it
    rather than whichever test happens to be running when the garbage collector next wakes up."""
    yield
    gc.collect()
