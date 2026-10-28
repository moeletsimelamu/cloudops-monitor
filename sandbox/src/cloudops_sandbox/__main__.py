"""Run the sandbox: ``python -m cloudops_sandbox`` (127.0.0.1:8310 unless SANDBOX_HOST/PORT)."""

import os

import uvicorn

from cloudops_sandbox.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("SANDBOX_HOST", "127.0.0.1"),
        port=int(os.environ.get("SANDBOX_PORT", "8310")),
    )


if __name__ == "__main__":
    main()
