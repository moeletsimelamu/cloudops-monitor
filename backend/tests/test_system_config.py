from pathlib import Path

from fastapi.testclient import TestClient

from cloudops.main import create_app
from tests.conftest import OPERATOR_TOKEN, make_settings, operator_headers

GITHUB_TOKEN = "github_pat_" + "Z" * 40


def test_effective_configuration_reports_secrets_only_as_set_or_not(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path / "data", github_token=GITHUB_TOKEN, allowed_targets=["localhost:8310"]
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/system/config", headers=operator_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["operator_token"] == "set"
    assert body["github"]["token"] == "set"
    assert body["allowed_targets"] == ["localhost:8310"]
    assert body["target_policy"] == "allowlist"
    assert OPERATOR_TOKEN not in response.text
    assert GITHUB_TOKEN not in response.text


def test_configuration_warnings_are_exposed_to_the_operator(client: TestClient) -> None:
    body = client.get("/api/system/config", headers=operator_headers()).json()
    assert body["github"]["token"] == "not set"
    assert any("CLOUDOPS_ALLOWED_TARGETS is empty" in w for w in body["warnings"])
    assert any("monitoring worker is disabled" in w for w in body["warnings"])
