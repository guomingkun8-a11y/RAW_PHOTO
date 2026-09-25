from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import sys
import threading


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.video.composition.composition_service import video_composition_service  # noqa: E402


LOGGER = logging.getLogger("video_composition_worker")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def main() -> None:
    log_level = getattr(logging, _clean(os.getenv("VIDEO_COMPOSITION_WORKER_LOG_LEVEL"), "INFO").upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="[%(levelname)s][%(asctime)s][%(name)s] %(message)s")
    if not video_composition_service.settings.enabled or not video_composition_service.settings.queue_enabled:
        raise SystemExit("video composition queue is disabled; set VIDEO_COMPOSITION_ENABLED=true and VIDEO_COMPOSITION_QUEUE_ENABLED=true")
    if video_composition_service.task_queue is None:
        raise SystemExit("video composition queue is not configured")

    stop_event = threading.Event()

    def stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    LOGGER.info("Starting video composition worker")
    video_composition_service.work_forever(stop_event=stop_event, timeout_secs=5)


if __name__ == "__main__":
    main()
