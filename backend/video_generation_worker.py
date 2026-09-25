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

from services.platform.config import config  # noqa: E402
from services.platform.database_maintenance import ensure_database_ready  # noqa: E402


LOGGER = logging.getLogger("video_generation_worker")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def main() -> None:
    log_level = getattr(logging, _clean(os.getenv("VIDEO_GENERATION_WORKER_LOG_LEVEL"), "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="[%(levelname)s][%(asctime)s][%(name)s] %(message)s",
    )
    os.environ["VIDEO_GENERATION_WORKER_PROCESS"] = "true"
    settings = config.get_video_generation_settings()
    if not bool(settings.get("enabled") and settings.get("queue_enabled")):
        raise SystemExit("video generation queue is disabled; set VIDEO_GENERATION_ENABLED=true and VIDEO_GENERATION_QUEUE_ENABLED=true")

    database_result = ensure_database_ready(strict=True, cleanup_sessions=False)
    LOGGER.info("Video generation database ready: %s", database_result.get("migrations", {}))

    # Importing the service creates its database-backed task store, so migrations
    # must finish first when this worker is started without the API process.
    from services.video.video_generation_service import video_generation_task_service

    if video_generation_task_service.task_queue is None:
        raise SystemExit("video generation queue is not configured")

    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    LOGGER.info("Starting video generation worker")
    video_generation_task_service.work_forever(stop_event=stop_event, timeout_secs=5)


if __name__ == "__main__":
    main()

