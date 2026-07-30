from __future__ import annotations

import signal
import sys
import threading
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.config import config


def main() -> None:
    settings = config.get_image_task_queue_settings()
    if not settings.get("enabled"):
        raise SystemExit("image task queue is disabled; set IMAGE_TASK_QUEUE_ENABLED=true to run the worker")

    # Queue recovery belongs to workers, not API replicas.
    os.environ["IMAGE_TASK_WORKER_PROCESS"] = "true"

    if settings.get("executor") == "celery":
        import subprocess

        # Bootstrap recovery once in the parent. Celery child processes inherit
        # the skip flag and rely on Redis/Celery for delivery thereafter.
        from services.image_task_service import image_task_service

        image_task_service.close()
        os.environ["IMAGE_TASK_SKIP_STARTUP_RECOVERY"] = "true"

        command = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "services.celery_app:celery_app",
            "worker",
            "--loglevel=INFO",
            f"--concurrency={max(1, int(settings.get('worker_concurrency') or 1))}",
        ]
        if os.name == "nt":
            command.append("--pool=solo")
        raise SystemExit(subprocess.call(command, cwd=str(BACKEND_DIR)))

    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    from services.image_task_service import image_task_service

    image_task_service.work_forever(
        stop_event=stop_event,
        timeout_secs=int(settings.get("worker_poll_timeout_secs") or 5),
    )


if __name__ == "__main__":
    main()
