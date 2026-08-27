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

from services.ecommerce.professional_video_service import professional_video_asset_service  # noqa: E402
from services.ecommerce.video_analysis_queue_service import (  # noqa: E402
    VideoAnalysisExecutionLease,
    VideoAnalysisQueueMessage,
    VideoAnalysisQueueUnavailable,
    video_analysis_queue_service,
)
from services.ecommerce.video_analysis_service import professional_video_analysis_service  # noqa: E402
from services.platform.runtime_requirements import validate_enterprise_runtime  # noqa: E402


LOGGER = logging.getLogger("professional_video_worker")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _lease_heartbeat(
    lease: VideoAnalysisExecutionLease,
    stop_event: threading.Event,
    finished: threading.Event,
) -> None:
    interval = max(10.0, min(60.0, video_analysis_queue_service.settings.slot_lease_secs / 3))
    while not stop_event.is_set() and not finished.wait(interval):
        if not video_analysis_queue_service.renew_execution(lease):
            return


def _retry_or_dead_letter(message: VideoAnalysisQueueMessage, error: BaseException | str) -> None:
    error_text = _clean(error, "video analysis worker failed")[:12000]
    next_attempt = message.attempt + 1
    if next_attempt <= video_analysis_queue_service.settings.max_retries:
        delay_secs = video_analysis_queue_service.retry_delay(next_attempt)
        LOGGER.warning(
            "Retrying video analysis %s after attempt %s in %ss: %s",
            message.video_id,
            next_attempt,
            delay_secs,
            error_text,
        )
        professional_video_asset_service.mark_analysis_queued(
            message.video_id,
            owner_id=message.owner_id,
            error=error_text,
        )
        video_analysis_queue_service.schedule_retry(
            message,
            attempt=next_attempt,
            reason=error_text,
            delay_secs=delay_secs,
        )
        return

    final_error = f"Video analysis failed after {next_attempt} attempts: {error_text}"
    professional_video_asset_service.mark_analysis_failed(
        message.video_id,
        owner_id=message.owner_id,
        error=final_error,
    )
    dead_id = video_analysis_queue_service.dead_letter(message, attempt=next_attempt, error=final_error)
    video_analysis_queue_service.release_pending(video_id=message.video_id, owner_id=message.owner_id)
    LOGGER.error(
        "Moved video analysis %s to dead letter %s after %s attempts: %s",
        message.video_id,
        dead_id,
        next_attempt,
        error_text,
    )


def _process_message(message: VideoAnalysisQueueMessage, *, stop_event: threading.Event) -> None:
    if not message.video_id:
        video_analysis_queue_service.ack(message)
        return
    current = professional_video_asset_service.get_video(message.video_id, owner_id=message.owner_id)
    if current is None:
        video_analysis_queue_service.release_pending(video_id=message.video_id, owner_id=message.owner_id)
        video_analysis_queue_service.ack(message)
        return
    if current.get("analysisStatus") == "ready":
        video_analysis_queue_service.release_pending(video_id=message.video_id, owner_id=message.owner_id)
        video_analysis_queue_service.ack(message)
        return

    lease = video_analysis_queue_service.acquire_execution(owner_id=message.owner_id)
    if lease is None:
        video_analysis_queue_service.defer(message)
        return

    finished = threading.Event()
    heartbeat = threading.Thread(
        target=_lease_heartbeat,
        args=(lease, stop_event, finished),
        name=f"video-analysis-lease-{message.video_id[-8:]}",
        daemon=True,
    )
    heartbeat.start()
    try:
        professional_video_analysis_service.analyze_video_safely(message.video_id, owner_id=message.owner_id)
        video_analysis_queue_service.release_pending(video_id=message.video_id, owner_id=message.owner_id)
        video_analysis_queue_service.ack(message)
    except Exception as exc:
        LOGGER.exception("Video analysis %s failed in worker", message.video_id)
        _retry_or_dead_letter(message, exc)
    finally:
        finished.set()
        heartbeat.join(timeout=1.0)
        video_analysis_queue_service.release_execution(lease)


def _worker_loop(index: int, stop_event: threading.Event) -> None:
    consumer = f"thread-{index}"
    worker_id = f"{os.getpid()}:{consumer}"
    try:
        while not stop_event.is_set():
            try:
                video_analysis_queue_service.touch_worker(worker_id)
                video_analysis_queue_service.promote_due_retries()
                message = video_analysis_queue_service.claim_stale(consumer=consumer)
                if message is None:
                    message = video_analysis_queue_service.dequeue(consumer=consumer, timeout_secs=5)
                if message is not None:
                    _process_message(message, stop_event=stop_event)
            except VideoAnalysisQueueUnavailable:
                stop_event.wait(2.0)
            except Exception:
                LOGGER.exception("Video analysis worker loop failed")
                stop_event.wait(1.0)
    finally:
        try:
            video_analysis_queue_service.forget_worker(worker_id)
        except Exception:
            pass


def main() -> None:
    log_level = getattr(logging, _clean(os.getenv("VIDEO_WORKER_LOG_LEVEL"), "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="[%(levelname)s][%(asctime)s][%(name)s] %(message)s",
    )
    validate_enterprise_runtime()
    settings = video_analysis_queue_service.settings
    if not settings.enabled:
        raise SystemExit("video analysis queue is disabled; set VIDEO_ANALYSIS_ENABLED=true and VIDEO_PARSE_QUEUE_ENABLED=true")
    os.environ["VIDEO_ANALYSIS_WORKER_PROCESS"] = "true"
    video_analysis_queue_service.ensure_ready()

    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    workers = [
        threading.Thread(
            target=_worker_loop,
            args=(index, stop_event),
            name=f"professional-video-worker-{index}",
            daemon=False,
        )
        for index in range(settings.worker_concurrency)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


if __name__ == "__main__":
    main()
