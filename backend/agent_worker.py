from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.ecommerce.agent_queue_service import (  # noqa: E402
    AgentExecutionLease,
    AgentQueueMessage,
    AgentQueueUnavailable,
    agent_queue_service,
)
from services.ecommerce.ecommerce_agent_memory_service import ecommerce_agent_memory_service  # noqa: E402
from services.platform.runtime_requirements import validate_enterprise_runtime  # noqa: E402


TERMINAL_STATUSES = {"waiting_for_input", "completed", "failed", "canceled"}
SUSPENDED_STATUSES = {"waiting_for_images"}
LOGGER = logging.getLogger("professional_agent_worker")


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _cleanup_message(message: AgentQueueMessage, state: dict[str, object] | None) -> None:
    owner_id = _clean((state or {}).get("ownerId"), message.owner_id)
    conversation_id = _clean((state or {}).get("conversationId"), message.conversation_id)
    agent_queue_service.release_pending(run_id=message.run_id, owner_id=owner_id)
    if conversation_id:
        agent_queue_service.release_conversation(
            run_id=message.run_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
        )


def _retry_or_dead_letter(
    message: AgentQueueMessage,
    *,
    state: dict[str, object] | None,
    error: BaseException | str,
) -> None:
    error_text = _clean(error, "professional agent worker failed")[:12000]
    next_attempt = message.attempt + 1
    if next_attempt <= agent_queue_service.settings.max_retries:
        delay_secs = agent_queue_service.retry_delay(next_attempt)
        LOGGER.warning(
            "Retrying Agent run %s after attempt %s in %ss: %s",
            message.run_id,
            next_attempt,
            delay_secs,
            error_text,
        )
        agent_queue_service.schedule_retry(
            message,
            attempt=next_attempt,
            reason=error_text,
            delay_secs=delay_secs,
        )
        return

    final_error = f"Agent worker failed after {next_attempt} attempts: {error_text}"
    try:
        from services.ecommerce.cow_agent_runtime_service import fail_queued_cow_agent_run

        failed_run = fail_queued_cow_agent_run(message.run_id, message.owner_id, final_error)
        if failed_run is not None:
            state = ecommerce_agent_memory_service.load_run_state(
                message.run_id,
                owner_id=message.owner_id,
            ) or state
    except Exception:
        LOGGER.exception("Could not persist terminal failure for Agent run %s", message.run_id)
    dead_id = agent_queue_service.dead_letter(message, attempt=next_attempt, error=final_error)
    _cleanup_message(message, state)
    LOGGER.error(
        "Moved Agent run %s to dead letter %s after %s attempts: %s",
        message.run_id,
        dead_id,
        next_attempt,
        error_text,
    )


def _lease_heartbeat(
    lease: AgentExecutionLease,
    stop_event: threading.Event,
    finished: threading.Event,
) -> None:
    interval = max(10.0, min(60.0, agent_queue_service.settings.slot_lease_secs / 3))
    while not stop_event.is_set() and not finished.wait(interval):
        if not agent_queue_service.renew_execution(lease):
            return


def _process_message(
    message: AgentQueueMessage,
    *,
    stop_event: threading.Event,
) -> None:
    try:
        state = ecommerce_agent_memory_service.load_run_state(message.run_id, owner_id=message.owner_id)
    except Exception as exc:
        _retry_or_dead_letter(message, state=None, error=exc)
        return
    if state is None:
        _cleanup_message(message, None)
        agent_queue_service.ack(message)
        return
    if _clean(state.get("status")) in TERMINAL_STATUSES:
        _cleanup_message(message, state)
        agent_queue_service.ack(message)
        return

    conversation_id = _clean(state.get("conversationId"))
    lease = agent_queue_service.acquire_execution(
        owner_id=message.owner_id,
        conversation_id=conversation_id,
    )
    if lease is None:
        agent_queue_service.defer(message)
        return

    finished = threading.Event()
    heartbeat = threading.Thread(
        target=_lease_heartbeat,
        args=(lease, stop_event, finished),
        name=f"agent-lease-{message.run_id[-8:]}",
        daemon=True,
    )
    heartbeat.start()
    try:
        from services.ecommerce.cow_agent_runtime_service import execute_queued_cow_agent_run

        run = execute_queued_cow_agent_run(message.run_id, message.owner_id)
        if run is not None and run.status.value in SUSPENDED_STATUSES:
            agent_queue_service.ack(message)
            return
        if run is None or run.status.value not in TERMINAL_STATUSES:
            _retry_or_dead_letter(
                message,
                state=state,
                error="Agent execution returned without a terminal or suspended state",
            )
            return
        state = ecommerce_agent_memory_service.load_run_state(message.run_id, owner_id=message.owner_id) or state
        _cleanup_message(message, state)
        agent_queue_service.ack(message)
    except Exception as exc:
        LOGGER.exception("Agent run %s failed in worker", message.run_id)
        _retry_or_dead_letter(message, state=state, error=exc)
    finally:
        finished.set()
        heartbeat.join(timeout=1.0)
        agent_queue_service.release_execution(lease)


def _worker_loop(index: int, stop_event: threading.Event) -> None:
    consumer = f"thread-{index}"
    worker_id = f"{os.getpid()}:{consumer}"
    try:
        while not stop_event.is_set():
            try:
                agent_queue_service.touch_worker(worker_id)
                agent_queue_service.promote_due_retries()
                message = agent_queue_service.claim_stale(consumer=consumer)
                if message is None:
                    message = agent_queue_service.dequeue(consumer=consumer, timeout_secs=5)
                if message is not None:
                    _process_message(message, stop_event=stop_event)
            except AgentQueueUnavailable:
                stop_event.wait(2.0)
            except Exception:
                stop_event.wait(1.0)
    finally:
        try:
            agent_queue_service.forget_worker(worker_id)
        except Exception:
            pass


def _memory_distiller_loop(stop_event: threading.Event) -> None:
    worker_id = f"{os.getpid()}:memory-distiller"
    while not stop_event.is_set():
        try:
            job = ecommerce_agent_memory_service.claim_memory_job(worker_id=worker_id)
            if job is None:
                stop_event.wait(1.5)
                continue
            try:
                from services.ecommerce.ecommerce_agent_memory_distiller import process_memory_job

                process_memory_job(job)
                ecommerce_agent_memory_service.finish_memory_job(job["jobId"], success=True)
                if job.get("jobType") == "conversation":
                    today = time.strftime("%Y-%m-%d")
                    ecommerce_agent_memory_service.enqueue_memory_job(
                        job_id=f"dream:{job['ownerId']}:{today}",
                        owner_id=str(job["ownerId"]),
                        job_type="dream",
                    )
            except Exception as exc:
                ecommerce_agent_memory_service.finish_memory_job(
                    job["jobId"],
                    success=False,
                    error=str(exc),
                )
        except Exception:
            stop_event.wait(2.0)


def main() -> None:
    log_level = getattr(logging, _clean(os.getenv("AGENT_WORKER_LOG_LEVEL"), "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="[%(levelname)s][%(asctime)s][%(name)s] %(message)s",
    )
    validate_enterprise_runtime()
    settings = agent_queue_service.settings
    if not settings.enabled:
        raise SystemExit("professional agent queue is disabled; set AGENT_QUEUE_ENABLED=true")
    os.environ["AGENT_WORKER_PROCESS"] = "true"
    ecommerce_agent_memory_service.ensure_ready()
    agent_queue_service.ensure_ready()

    stop_event = threading.Event()
    memory_distiller = threading.Thread(
        target=_memory_distiller_loop,
        args=(stop_event,),
        name="professional-agent-memory-distiller",
        daemon=False,
    )
    memory_distiller.start()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    workers = [
        threading.Thread(
            target=_worker_loop,
            args=(index, stop_event),
            name=f"professional-agent-worker-{index}",
            daemon=False,
        )
        for index in range(settings.worker_concurrency)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    memory_distiller.join(timeout=2.0)


if __name__ == "__main__":
    main()
