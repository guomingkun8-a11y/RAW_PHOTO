"""Standalone worker for periodic operational checks and alert delivery."""

from __future__ import annotations

import logging
from pathlib import Path
import signal
import sys
import threading


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Import the application config before the monitor services so .env.local is
# loaded when this module is started as a standalone process.
from services.platform.config import config  # noqa: F401, E402
from services.alerts.alert_service import alert_service  # noqa: E402
from services.platform.runtime_requirements import validate_enterprise_runtime  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][%(asctime)s][operational-monitor] %(message)s",
    )
    validate_enterprise_runtime()
    stop_event = threading.Event()

    def stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    alert_service.run_forever(stop_event)


if __name__ == "__main__":
    main()
