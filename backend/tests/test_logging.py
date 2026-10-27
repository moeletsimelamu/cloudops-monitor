import json
import logging
import sys

from cloudops.domain.redaction import REDACTED
from cloudops.logging_setup import JsonFormatter, RedactingTextFormatter, request_id_var

SECRET = "configured-secret-value-1234567890"


def _record(
    message: str, *args: object, fields: dict[str, object] | None = None
) -> logging.LogRecord:
    record = logging.LogRecord("cloudops.test", logging.WARNING, __file__, 1, message, args, None)
    record.request_id = "req-1"
    if fields is not None:
        record.fields = fields
    return record


def test_json_lines_carry_level_logger_request_id_and_fields() -> None:
    line = JsonFormatter().format(_record("checked %s", "orders", fields={"duration_ms": 84}))
    entry = json.loads(line)
    assert entry["level"] == "warning"
    assert entry["logger"] == "cloudops.test"
    assert entry["message"] == "checked orders"
    assert entry["request_id"] == "req-1"
    assert entry["duration_ms"] == 84
    assert entry["timestamp"].endswith("+00:00")


def test_json_output_masks_secrets_in_message_fields_and_exceptions() -> None:
    formatter = JsonFormatter([SECRET])
    record = _record(
        "calling with %s", SECRET, fields={"authorization": "Bearer abcdefghijk", "x": SECRET}
    )
    try:
        raise RuntimeError(f"failed with token={SECRET}")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    line = formatter.format(record)
    assert SECRET not in line
    entry = json.loads(line)
    assert entry["message"] == f"calling with {REDACTED}"
    assert entry["authorization"] == REDACTED
    assert entry["x"] == REDACTED
    assert "RuntimeError" in entry["exception"]


def test_text_format_is_redacted_too() -> None:
    formatter = RedactingTextFormatter([SECRET])
    line = formatter.format(_record("token is %s", SECRET, fields={"event": "x"}))
    assert SECRET not in line
    assert "event=x" in line
    assert "[req-1]" in line


def test_request_id_defaults_to_dash() -> None:
    assert request_id_var.get() == "-"
