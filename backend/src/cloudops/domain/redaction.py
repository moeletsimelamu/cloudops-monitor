"""Masking of credentials in text and structured attributes.

Used for CloudOps Monitor's own log output and for log entries that applications ship to it.
Pattern-based redaction lowers the risk of storing a secret; it cannot recognise every secret or
every piece of personal data, so applications remain responsible for what they log.
"""

import re
from collections.abc import Iterable, Mapping

REDACTED = "[REDACTED]"

# Substrings of attribute names whose values are always masked.
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "session",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "signature",
)
_SENSITIVE_KEYS_EXACT = frozenset({"auth", "pwd", "key"})

_NAMED_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)"
    r"(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private keys, through the END line, or to the end of the text if it was cut short.
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
            r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY-----|.*\Z)",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # Slack, Stripe (secret and restricted) and Google API keys.
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{35}"), REDACTED),
    # Authorization header values.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # JSON Web Tokens (three base64url segments, the first starting with '{"').
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    # GitHub tokens.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), REDACTED),
    # AWS access key IDs.
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    # CloudOps ingest keys.
    (re.compile(r"\bcok_[A-Za-z0-9_-]{16,}"), REDACTED),
    # Credentials embedded in URLs: scheme://user:password@host
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"), f"{REDACTED}@"),
    # password=..., "token": "...", api_key: ...
    (_NAMED_VALUE, rf"\1\2{REDACTED}"),
)


def redact_text(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Mask credential-shaped substrings and any of ``known_secrets`` (exact values)."""
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_sensitive_key(key: str) -> bool:
    normalised = key.strip().lower().replace("-", "_").replace(" ", "_")
    if normalised in _SENSITIVE_KEYS_EXACT:
        return True
    return any(part in normalised for part in _SENSITIVE_KEY_PARTS)


def redact_attributes(
    attributes: Mapping[str, object], known_secrets: Iterable[str] = ()
) -> dict[str, object]:
    """Mask values under sensitive keys and credential-shaped strings in the other values, at
    any depth: CloudOps's own log records carry lists and nested objects."""
    secrets = tuple(known_secrets)
    return {
        key: REDACTED if is_sensitive_key(key) else _redact_value(value, secrets)
        for key, value in attributes.items()
    }


def _redact_value(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return redact_attributes({str(k): v for k, v in value.items()}, secrets)
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(item, secrets) for item in value]
    return value
