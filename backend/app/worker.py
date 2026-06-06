"""Background worker entrypoint.

This is intentionally minimal until the Postgres outbox processor is implemented.
The Docker Compose worker service runs this module so the full target service
topology can start from one command.
"""

from __future__ import annotations

import logging
import os
import signal
import time


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("techbold.worker")
shutdown_requested = False


def _request_shutdown(signum: int, _frame: object) -> None:
    global shutdown_requested
    shutdown_requested = True
    logger.info("worker shutdown requested", extra={"signal": signum})


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    logger.info("worker started")
    while not shutdown_requested:
        time.sleep(5)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
