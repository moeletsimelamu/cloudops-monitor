"""Application settings, read from environment variables prefixed with ``CLOUDOPS_``.

Field-level bounds are enforced by Pydantic. Rules that involve several settings, or that should
produce a readable message instead of a validation dump, live in :func:`check_settings`, which
returns fatal errors (the app refuses to start) and warnings (shown in logs and the System view).
Neither ever includes a secret value.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MIN_OPERATOR_TOKEN_LENGTH = 32
# The placeholder in .env.example: refused, so a copied example file cannot run with a known token.
EXAMPLE_OPERATOR_TOKEN = "replace-with-output-of-cloudops-generate-token"  # noqa: S105 - not a secret

# host:port, where host is a DNS name, an IPv4 address or a bracketed IPv6 address.
_TARGET_ENTRY = re.compile(
    r"^(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?):[0-9]{1,5}$"
)


def _split_csv(value: object) -> object:
    """Accept ``a,b,c`` (friendlier in .env files) as well as a list."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLOUDOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage
    data_dir: Path = Path("data")
    # Built frontend to serve from this process (container). Unset in development, where the
    # Angular dev server is used instead.
    static_dir: Path | None = None
    # Apply pending database migrations at start-up. Turn off to run `cloudops db upgrade` as a
    # separate step.
    auto_migrate: bool = True

    # Operator authentication
    auth_mode: Literal["token", "disabled"] = "token"
    operator_token: SecretStr | None = None
    session_hours: int = Field(default=12, ge=1, le=168)

    # Host names this server answers for (protects against DNS rebinding). Add the public name
    # when served under one; "*" disables the check.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"]
    )

    # Monitoring targets (see docs/SECURITY.md §3)
    target_policy: Literal["allowlist", "public"] = "allowlist"
    allowed_targets: Annotated[list[str], NoDecode] = Field(default_factory=list)
    worker_enabled: bool = True
    monitor_max_concurrency: int = Field(default=10, ge=1, le=50)

    # Deployment records from GitHub Actions
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_sync_seconds: int = Field(default=300, ge=60, le=3600)

    # Retention
    check_retention_days: int = Field(default=30, ge=1, le=365)
    event_retention_days: int = Field(default=90, ge=1, le=3650)
    log_retention_days: int = Field(default=7, ge=1, le=90)
    log_max_rows: int = Field(default=200_000, ge=1_000, le=10_000_000)

    # Operations
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    api_docs: bool = True

    @field_validator("operator_token", "github_token", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """``CLOUDOPS_GITHUB_TOKEN=`` (as copied from .env.example) means "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("allowed_hosts", "allowed_targets", mode="before")
    @classmethod
    def _parse_lists(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("allowed_hosts", "allowed_targets")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @property
    def database_path(self) -> Path:
        return self.data_dir / "cloudops.db"

    def secret_values(self) -> list[str]:
        """Every configured secret, for the log redaction filter."""
        secrets = (self.operator_token, self.github_token)
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]


@dataclass
class SettingsReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ConfigurationError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Invalid configuration:\n- " + "\n- ".join(errors))
        self.errors = errors


def check_settings(settings: Settings) -> SettingsReport:
    report = SettingsReport()

    if settings.auth_mode == "token":
        token = settings.operator_token.get_secret_value() if settings.operator_token else ""
        if not token:
            report.errors.append(
                "CLOUDOPS_OPERATOR_TOKEN is required when CLOUDOPS_AUTH_MODE=token. "
                "Generate one with `cloudops generate-token`, or set CLOUDOPS_AUTH_MODE=disabled "
                "for local development."
            )
        elif token == EXAMPLE_OPERATOR_TOKEN:
            report.errors.append(
                "CLOUDOPS_OPERATOR_TOKEN is still the placeholder from .env.example. Replace it "
                "with the output of `cloudops generate-token`."
            )
        elif len(token) < MIN_OPERATOR_TOKEN_LENGTH:
            report.errors.append(
                f"CLOUDOPS_OPERATOR_TOKEN must be at least {MIN_OPERATOR_TOKEN_LENGTH} "
                "characters long."
            )
    else:
        report.warnings.append(
            "Authentication is disabled (CLOUDOPS_AUTH_MODE=disabled). Anyone who can reach this "
            "server can change its configuration. Use this only on your own machine."
        )

    bad_targets = [t for t in settings.allowed_targets if not _TARGET_ENTRY.fullmatch(t)]
    if bad_targets:
        report.errors.append(
            "CLOUDOPS_ALLOWED_TARGETS entries must look like host:port "
            f"(invalid: {', '.join(bad_targets)})."
        )
    elif settings.target_policy == "allowlist" and not settings.allowed_targets:
        report.warnings.append(
            "CLOUDOPS_ALLOWED_TARGETS is empty and the target policy is 'allowlist', so no "
            "application can be registered for monitoring yet."
        )

    if not settings.github_api_url.startswith("https://"):
        report.errors.append("CLOUDOPS_GITHUB_API_URL must use https.")
    if settings.github_token is None:
        report.warnings.append(
            "CLOUDOPS_GITHUB_TOKEN is not set: deployment records can be read from public "
            "repositories only, within GitHub's limit of 60 requests per hour."
        )
    if not settings.worker_enabled:
        report.warnings.append(
            "The monitoring worker is disabled (CLOUDOPS_WORKER_ENABLED=false): no health checks "
            "will run and every application will become stale."
        )
    return report
