import pytest

from cloudops.domain.redaction import (
    REDACTED,
    is_sensitive_key,
    redact_attributes,
    redact_text,
)

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-ghi_jkl012", "abc.def-ghi_jkl012"),
        ("auth header was Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        (f"token in body {JWT}", JWT),
        ("pushed with ghp_" + "a1B2" * 9, "ghp_" + "a1B2" * 9),
        ("fine-grained github_pat_" + "x" * 30, "github_pat_" + "x" * 30),
        ("aws key AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE"),
        ("ingest cok_abcdefghijklmnopqrstuvwx", "cok_abcdefghijklmnopqrstuvwx"),
        ("connect postgres://admin:hunter2@db.internal/app", "admin:hunter2"),
        ("login failed password=hunter2 for user", "hunter2"),
        ('{"api_key": "k-123456"}', "k-123456"),
        ("client_secret: 's3cr3t value'", "s3cr3t value"),
    ],
)
def test_credentials_are_masked_in_text(text: str, secret: str) -> None:
    redacted = redact_text(text)
    assert secret not in redacted
    assert REDACTED in redacted


def test_ordinary_text_is_unchanged() -> None:
    text = "GET /api/orders -> 500 in 84 ms (request 3f2a, commit a1b2c3d)"
    assert redact_text(text) == text


def test_known_secret_values_are_masked_anywhere() -> None:
    assert (
        redact_text("value=plain-looking-token!", ["plain-looking-token!"]) == f"value={REDACTED}"
    )
    assert redact_text("nothing here", ["", "absent"]) == "nothing here"


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "DB_PASSWORD",
        "x-api-key",
        "apiKey",
        "Authorization",
        "set-cookie",
        "session_id",
        "refresh token",
        "aws_secret_access_key",
        "auth",
        "pwd",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["status_code", "path", "user_agent", "author", "duration_ms"])
def test_ordinary_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_attributes_mask_sensitive_keys_and_credential_values_but_keep_types() -> None:
    attributes = {
        "status_code": 500,
        "ok": False,
        "password": "hunter2",
        "note": "retry with Bearer abcdefgh12345",
        "ratio": 0.5,
    }
    assert redact_attributes(attributes) == {
        "status_code": 500,
        "ok": False,
        "password": REDACTED,
        "note": f"retry with Bearer {REDACTED}",
        "ratio": 0.5,
    }
