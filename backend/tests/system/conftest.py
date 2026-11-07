"""System tests: CloudOps Monitor as a real process, against real local targets.

They are slower than the rest of the suite (each starts servers, and some wait out real
timeouts), so they carry the ``system`` marker: ``pytest -m "not system"`` skips them.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.servers import ServerProcess
from tests.system.harness import CloudOpsProcess


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.system)


@pytest.fixture
def target() -> Iterator[ServerProcess]:
    """A controllable HTTP target in its own process (see tests/servers.py)."""
    with ServerProcess() as server:
        yield server


@pytest.fixture
def cloudops(tmp_path: Path, target: ServerProcess) -> Iterator[CloudOpsProcess]:
    """A server whose allowlist permits ``target``. Not started: the test decides when."""
    server = CloudOpsProcess(tmp_path, CLOUDOPS_ALLOWED_TARGETS=f"127.0.0.1:{target.port}")
    try:
        yield server
    finally:
        server.close()
