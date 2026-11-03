import logging
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cloudops_sandbox.app import create_app, logger
from cloudops_sandbox.log_shipper import LogShipper, to_entry


class Collector:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.failing = False
        self.lock = threading.Lock()

    def __call__(self, entries: list[dict[str, Any]]) -> None:
        if self.failing:
            raise OSError("connection refused")
        with self.lock:
            self.batches.append(entries)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [entry for batch in self.batches for entry in batch]


def _logger(handler: logging.Handler) -> logging.Logger:
    log = logging.getLogger(f"test.shipper.{id(handler)}")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    return log


def test_records_become_entries_with_structured_fields() -> None:
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "order %s failed", ("7",), None)
    record.event = "order.failed"
    record.correlation_id = "req-1"
    record.attributes = {"status_code": 500}
    entry = to_entry(record)
    assert entry["severity"] == "warning"
    assert entry["message"] == "order 7 failed"
    assert entry["event"] == "order.failed"
    assert entry["correlation_id"] == "req-1"
    assert entry["attributes"] == {"status_code": 500}
    assert entry["timestamp"].endswith("+00:00")


def test_plain_records_have_only_the_required_fields() -> None:
    record = logging.LogRecord("x", logging.CRITICAL, __file__, 1, "down", (), None)
    assert set(to_entry(record)) == {"timestamp", "severity", "message"}
    assert to_entry(record)["severity"] == "critical"


def test_close_sends_what_is_queued_in_batches() -> None:
    collector = Collector()
    shipper = LogShipper(collector, flush_seconds=60, max_batch=2)
    log = _logger(shipper)
    for index in range(5):
        log.info("entry %d", index)
    shipper.close()
    assert [len(batch) for batch in collector.batches] == [2, 2, 1]
    assert [e["message"] for e in collector.entries] == [f"entry {i}" for i in range(5)]
    assert shipper.sent == 5


def test_a_failing_monitor_drops_batches_without_raising(
    capsys: pytest.CaptureFixture[str],
) -> None:
    collector = Collector()
    collector.failing = True
    shipper = LogShipper(collector, flush_seconds=60)
    log = _logger(shipper)
    log.info("lost")
    log.info("also lost")
    shipper.close()
    assert shipper.dropped == 2
    assert shipper.sent == 0
    assert "connection refused" in capsys.readouterr().err


def test_a_full_queue_drops_instead_of_blocking() -> None:
    collector = Collector()
    shipper = LogShipper(collector, flush_seconds=60, max_queue=3)
    log = _logger(shipper)
    for _ in range(10):
        log.info("entry")
    assert shipper.dropped == 7
    shipper.close()
    assert shipper.sent == 3


def test_each_request_is_logged_with_its_outcome() -> None:
    collector = Collector()
    with TestClient(create_app(collector)) as client:
        client.get("/api/orders", headers={"X-Request-ID": "req-42"})
        client.put("/control/mode", json={"mode": "errors"})
        client.get("/api/orders", headers={"X-Request-ID": "req-43"})
        client.get("/health")
    requests = [e for e in collector.entries if e.get("event") == "http.request"]
    assert [(e["attributes"]["path"], e["attributes"]["status_code"]) for e in requests] == [
        ("/api/orders", 200),
        ("/api/orders", 500),
        ("/health", 500),
    ]
    assert requests[0]["severity"] == "info"
    assert requests[0]["correlation_id"] == "req-42"
    assert requests[1]["severity"] == "error"
    assert requests[0]["attributes"]["method"] == "GET"
    assert "synthetic" not in requests[0]["attributes"]
    # /control requests are not application traffic.
    assert all(e["attributes"]["path"] != "/control/mode" for e in requests)


def test_failures_log_an_error_with_the_same_correlation_id() -> None:
    collector = Collector()
    with TestClient(create_app(collector)) as client:
        client.put("/control/mode", json={"mode": "errors"})
        response = client.get("/api/orders", headers={"X-Request-ID": "req-9"})
    assert response.headers["X-Request-ID"] == "req-9"
    related = [e for e in collector.entries if e.get("correlation_id") == "req-9"]
    assert [e["event"] for e in related] == ["order.load_failed", "http.request"]
    assert related[0]["severity"] == "error"
    assert "database timeout" in related[0]["message"]


def test_synthetic_requests_are_labelled() -> None:
    collector = Collector()
    with TestClient(create_app(collector)) as client:
        client.get("/api/orders", headers={"X-Sandbox-Synthetic": "1"})
    assert collector.entries[0]["attributes"]["synthetic"] is True


def test_no_shipping_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_CLOUDOPS_URL", raising=False)
    monkeypatch.delenv("SANDBOX_INGEST_KEY", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/api/orders").status_code == 200
        assert not logger.handlers
