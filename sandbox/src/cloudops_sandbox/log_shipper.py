"""Ships Python log records to CloudOps Monitor. Standard library only, so it can be copied into
any Python application.

Records are queued and sent in batches from a background thread, so logging never waits for the
network. If CloudOps Monitor is unreachable the batch is dropped and counted: losing log lines is
better than an application slowing down or running out of memory because its monitor is down.
"""

import json
import logging
import queue
import sys
import threading
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

PostBatch = Callable[[list[dict[str, Any]]], None]

_SEVERITIES = {"DEBUG": "debug", "INFO": "info", "WARNING": "warning", "ERROR": "error"}


def http_poster(base_url: str, ingest_key: str, timeout: float = 5.0) -> PostBatch:
    url = base_url.rstrip("/") + "/api/ingest/logs"

    def post(entries: list[dict[str, Any]]) -> None:
        request = urllib.request.Request(  # noqa: S310 - the operator configures the URL
            url,
            data=json.dumps({"entries": entries}).encode(),
            headers={
                "Authorization": f"Bearer {ingest_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
            pass

    return post


def to_entry(record: logging.LogRecord) -> dict[str, Any]:
    """A log record in CloudOps Monitor's format. Structured fields come from ``extra=``:
    ``event``, ``correlation_id`` and ``attributes``."""
    entry: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
        "severity": _SEVERITIES.get(record.levelname, "critical"),
        "message": record.getMessage()[:4096],
    }
    for name in ("event", "correlation_id", "attributes"):
        value = getattr(record, name, None)
        if value is not None:
            entry[name] = value
    return entry


class LogShipper(logging.Handler):
    def __init__(
        self,
        post: PostBatch,
        *,
        flush_seconds: float = 2.0,
        max_batch: int = 200,
        max_queue: int = 5000,
    ) -> None:
        super().__init__()
        self.post = post
        self.flush_seconds = flush_seconds
        self.max_batch = max_batch
        self.dropped = 0
        self.sent = 0
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="log-shipper", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(to_entry(record))
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self._stop.wait(self.flush_seconds):
            self._drain()
        self._drain()

    def _drain(self) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            while len(batch) < self.max_batch:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return
            try:
                self.post(batch)
                self.sent += len(batch)
            except Exception as error:
                self.dropped += len(batch)
                if not self._warned:  # once, and not through logging: that would feed this queue
                    self._warned = True
                    print(f"log shipper: batch dropped: {error}", file=sys.stderr)

    def close(self) -> None:
        """Send what is queued and stop."""
        self._stop.set()
        self._thread.join(timeout=10)
        super().close()
