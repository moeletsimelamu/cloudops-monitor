from pathlib import Path

import pytest

from cloudops.config import ConfigurationError, Settings, check_settings, load_settings
from cloudops.main import create_app
from tests.conftest import OPERATOR_TOKEN, make_settings


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CLOUDOPS_AUTH_MODE", "CLOUDOPS_TARGET_POLICY", "CLOUDOPS_ALLOWED_TARGETS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.auth_mode == "token"
    assert settings.target_policy == "allowlist"
    assert settings.allowed_targets == []
    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]
    assert settings.trusted_proxies == []  # nobody's forwarded headers are believed


def test_lists_accept_comma_separated_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDOPS_ALLOWED_TARGETS", "localhost:8310, Sandbox:8310,,")
    monkeypatch.setenv("CLOUDOPS_ALLOWED_HOSTS", "monitor.example.org,localhost")
    settings = Settings(_env_file=None)
    assert settings.allowed_targets == ["localhost:8310", "sandbox:8310"]
    assert settings.allowed_hosts == ["monitor.example.org", "localhost"]


def test_blank_secrets_count_as_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDOPS_GITHUB_TOKEN", "")
    monkeypatch.setenv("CLOUDOPS_OPERATOR_TOKEN", "   ")
    settings = Settings(_env_file=None)
    assert settings.github_token is None
    assert settings.operator_token is None


def test_example_env_file_loads_and_its_placeholder_token_is_refused() -> None:
    example = Path(__file__).parents[2] / ".env.example"
    settings = Settings(_env_file=example)
    assert settings.github_token is None
    assert settings.allowed_targets == ["localhost:8310"]
    errors = check_settings(settings).errors
    assert len(errors) == 1
    assert "still the placeholder from .env.example" in errors[0]


def test_valid_configuration_has_no_errors(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        allowed_targets=["localhost:8310", "[::1]:8000"],
        trusted_proxies=["172.18.0.0/16", "10.0.0.2", "fd00::/64"],
    )
    assert check_settings(settings).errors == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"operator_token": None}, "CLOUDOPS_OPERATOR_TOKEN is required"),
        ({"operator_token": ""}, "CLOUDOPS_OPERATOR_TOKEN is required"),
        ({"operator_token": "short"}, "at least 32 characters"),
        ({"allowed_targets": ["localhost"]}, "invalid: localhost"),
        ({"allowed_targets": ["http://x:80"]}, "must look like host:port"),
        ({"github_api_url": "http://api.github.com"}, "must use https"),
        ({"trusted_proxies": ["proxy.internal"]}, "invalid: proxy.internal"),
        ({"trusted_proxies": ["*"]}, "invalid: *"),
        ({"trusted_proxies": ["10.0.0.1", "0.0.0.0/0"]}, "invalid: 0.0.0.0/0"),
        ({"trusted_proxies": ["::/0"]}, "invalid: ::/0"),
    ],
)
def test_invalid_configuration_is_reported(
    tmp_path: Path, overrides: dict[str, object], expected: str
) -> None:
    errors = check_settings(make_settings(tmp_path, **overrides)).errors
    assert any(expected in error for error in errors), errors


def test_warnings_explain_reduced_functionality(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_mode="disabled", operator_token=None)
    warnings = " ".join(check_settings(settings).warnings)
    assert "Authentication is disabled" in warnings
    assert "CLOUDOPS_ALLOWED_TARGETS is empty" in warnings
    assert "CLOUDOPS_GITHUB_TOKEN is not set" in warnings
    assert "monitoring worker is disabled" in warnings


def test_app_refuses_to_start_with_invalid_configuration_without_echoing_secrets(
    tmp_path: Path,
) -> None:
    secret = "too-short-secret"
    with pytest.raises(ConfigurationError) as caught:
        create_app(make_settings(tmp_path, operator_token=secret))
    assert "at least 32 characters" in str(caught.value)
    assert secret not in str(caught.value)


def test_secret_values_lists_only_configured_secrets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, github_token="ghp_" + "x" * 36)
    assert settings.secret_values() == [OPERATOR_TOKEN, "ghp_" + "x" * 36]
    assert make_settings(tmp_path, auth_mode="disabled", operator_token=None).secret_values() == []


def test_unparseable_values_name_the_variable_and_never_repeat_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDOPS_SESSION_HOURS", "twelve-secret-ish")
    monkeypatch.setenv("CLOUDOPS_AUTH_MODE", "tokens")
    with pytest.raises(ConfigurationError) as caught:
        load_settings(_env_file=None)
    errors = caught.value.errors
    assert any(
        e.startswith("CLOUDOPS_SESSION_HOURS: Input should be a valid integer") for e in errors
    )
    assert any(
        e.startswith("CLOUDOPS_AUTH_MODE: Input should be 'token' or 'disabled'") for e in errors
    )
    assert "twelve-secret-ish" not in str(caught.value)
