from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import tempfile
import threading
import time
from pathlib import Path
import sys
import uuid
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.image_task_queue import RedisImageTaskQueue
from services.image_task_service import ImageTaskService
from services.image_task_store import DatabaseImageTaskStore


TERMINAL_STATUSES = {"success", "error", "canceled"}


def _service(
    database_url: str,
    queue: RedisImageTaskQueue,
    *,
    delay_secs: float,
    fail_every: int,
    owner_concurrency: int,
    owner_pending_limit: int,
    handler_lock: threading.Lock,
    calls: list[int],
) -> ImageTaskService:
    def handler(_payload):
        time.sleep(delay_secs)
        with handler_lock:
            calls[0] += 1
            call_number = calls[0]
        if fail_every > 0 and call_number % fail_every == 0:
            raise RuntimeError("mock upstream failure")
        return {"data": [{"url": "http://load-test.invalid/image.png"}]}

    service = ImageTaskService(
        Path(tempfile.gettempdir()) / f"image-load-{uuid.uuid4().hex}.json",
        task_store=DatabaseImageTaskStore(database_url),
        task_queue=queue,
        run_inline=False,
        generation_handler=handler,
        edit_handler=handler,
        relay_enabled_getter=lambda: False,
        max_retries_getter=lambda: 0,
    )
    service._owner_concurrency = max(1, int(owner_concurrency))
    service._owner_pending_limit = max(1, int(owner_pending_limit))
    return service


def _is_load_test_task(task: dict[str, Any]) -> bool:
    owner_id = str(task.get("owner_id") or "")
    task_id = str(task.get("id") or "")
    return (
        owner_id.startswith("load-user-")
        or owner_id in {"load-duplicate-owner", "load-burst-owner"}
        or task_id.startswith("load-task-")
        or task_id.startswith("owner-burst-task-")
    )


def _load_test_tasks(service: ImageTaskService) -> list[dict[str, Any]]:
    tasks = service.task_store.load_all()
    return [task for task in tasks.values() if isinstance(task, dict) and _is_load_test_task(task)]


def _status_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _load_test_keys(service: ImageTaskService) -> list[str]:
    keys: list[str] = []
    for key, task in service.task_store.load_all().items():
        if isinstance(task, dict) and _is_load_test_task(task):
            keys.append(str(key))
    return keys


def _build_recommendations(
    *,
    total_concurrency: int,
    workers: int,
    owner_concurrency: int,
    max_queue_depth: int,
    avg_slot_utilization: float,
    peak_slot_utilization: float,
    max_owner_queued: int,
    failure_rate: float,
) -> list[str]:
    recommendations: list[str] = []
    if failure_rate >= 0.05:
        recommendations.append(
            "failure_rate >= 5%; lower total_concurrency first and check upstream/account pool errors."
        )
    if max_queue_depth > total_concurrency * 3 and peak_slot_utilization >= 0.85:
        recommendations.append(
            "queue backs up while slots are busy; increase worker_concurrency/worker replicas only if accounts can handle it."
        )
    if max_queue_depth > total_concurrency * 3 and avg_slot_utilization < 0.5:
        recommendations.append(
            "queue backs up but slots are underused; check worker count, Redis delivery, and Celery process health."
        )
    if max_owner_queued > owner_concurrency * 2:
        recommendations.append(
            "single user queue is visible; keep owner_concurrency low for fairness, raise it only for trusted internal users."
        )
    if peak_slot_utilization < 0.6 and max_queue_depth <= total_concurrency:
        recommendations.append("current concurrency is enough for this test shape; do not raise limits yet.")
    if workers > total_concurrency:
        recommendations.append("workers exceed total_concurrency; extra workers are standby unless total_concurrency is raised.")
    if not recommendations:
        recommendations.append("metrics look balanced for this mock load; keep the current limits and test with real upstream latency.")
    return recommendations


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a mock image task load test without upstream image generation.")
    parser.add_argument("--users", type=int, default=60)
    parser.add_argument("--api-instances", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--total-concurrency", type=int, default=0)
    parser.add_argument("--owner-concurrency", type=int, default=2)
    parser.add_argument("--owner-pending-limit", type=int, default=50)
    parser.add_argument("--owner-burst-tasks", type=int, default=8)
    parser.add_argument("--handler-delay-ms", type=float, default=250)
    parser.add_argument("--fail-every", type=int, default=0)
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--duplicate-probes", type=int, default=20)
    parser.add_argument("--sample-interval-ms", type=float, default=50)
    parser.add_argument("--timeout-secs", type=float, default=0)
    args = parser.parse_args()
    if args.users < 1 or args.api_instances < 1 or args.workers < 1:
        raise SystemExit("users, api-instances and workers must be positive")

    total_concurrency = max(1, int(args.total_concurrency or args.workers))
    queue_name = f"codex-load-{uuid.uuid4().hex[:12]}"
    queue = RedisImageTaskQueue(args.redis_url, queue_name=queue_name, max_concurrency=total_concurrency)
    queue._client.delete(queue_name, queue._slot_key, queue._worker_key)
    calls = [0]
    call_lock = threading.Lock()
    temp_context = tempfile.TemporaryDirectory() if not args.database_url else None
    database_url = args.database_url or f"sqlite:///{Path(temp_context.name) / 'tasks.db'}"
    service_kwargs = {
        "delay_secs": args.handler_delay_ms / 1000,
        "fail_every": max(0, int(args.fail_every)),
        "owner_concurrency": max(1, int(args.owner_concurrency)),
        "owner_pending_limit": max(1, int(args.owner_pending_limit)),
        "handler_lock": call_lock,
        "calls": calls,
    }
    api_services = [
        _service(database_url, queue, **service_kwargs)
        for _ in range(args.api_instances)
    ]
    worker_services = [
        _service(database_url, queue, **service_kwargs)
        for _ in range(args.workers)
    ]

    submitted = 0
    submit_errors = 0
    duplicate_results: list[dict[str, object]] = []
    owner_id = "load-burst-owner"
    samples: list[dict[str, object]] = []
    sampler_stop = threading.Event()
    started = time.perf_counter()
    total_unique_tasks = args.users + 1 + max(0, int(args.owner_burst_tasks))
    terminal_ids: set[str] = set()
    terminal_lock = threading.Lock()
    worker_timeout = args.timeout_secs or max(20.0, total_unique_tasks * (args.handler_delay_ms / 1000) * 6 / args.workers)

    def sample_loop() -> None:
        interval = max(0.01, args.sample_interval_ms / 1000)
        service = worker_services[0]
        while not sampler_stop.is_set():
            try:
                snapshot = service.monitoring_snapshot()
            except Exception:
                snapshot = {}
            owner_rows = snapshot.get("owner_activity") if isinstance(snapshot.get("owner_activity"), list) else []
            owner_row = next(
                (row for row in owner_rows if isinstance(row, dict) and row.get("owner_id") == owner_id),
                {},
            )
            samples.append(
                {
                    "queue_depth": int(snapshot.get("queue_depth") or 0),
                    "active_slots": int(snapshot.get("active_slots") or 0),
                    "active_workers": int(snapshot.get("active_workers") or 0),
                    "queued_tasks": int(snapshot.get("queued_tasks") or 0),
                    "running_tasks": int(snapshot.get("running_tasks") or 0),
                    "owner_queued_tasks": int(owner_row.get("queued_tasks") or 0),
                    "owner_running_tasks": int(owner_row.get("running_tasks") or 0),
                    "owner_active_tasks": int(owner_row.get("active_tasks") or 0),
                }
            )
            sampler_stop.wait(interval)

    sampler = threading.Thread(target=sample_loop, name="load-test-sampler", daemon=True)
    sampler.start()
    try:
        def submit(index: int) -> dict[str, object]:
            service = api_services[index % len(api_services)]
            return service.submit_generation(
                {"id": f"load-user-{index}"},
                client_task_id=f"load-task-{index}",
                prompt="mock load test",
                model="gpt-image-2",
                size=None,
                base_url="http://load-test.invalid",
            )

        with ThreadPoolExecutor(max_workers=min(64, args.users)) as executor:
            futures = [executor.submit(submit, index) for index in range(args.users)]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result.get("status") == "queued":
                        submitted += 1
                except Exception:
                    submit_errors += 1

        duplicate_owner = {"id": "load-duplicate-owner"}
        duplicate_id = "load-duplicate-task"
        with ThreadPoolExecutor(max_workers=min(16, max(2, args.duplicate_probes))) as executor:
            futures = [
                executor.submit(
                    api_services[index % len(api_services)].submit_generation,
                    duplicate_owner,
                    client_task_id=duplicate_id,
                    prompt="duplicate probe",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://load-test.invalid",
                )
                for index in range(max(2, args.duplicate_probes))
            ]
            duplicate_results = [future.result() for future in futures]

        owner_burst_tasks = max(0, int(args.owner_burst_tasks))
        with ThreadPoolExecutor(max_workers=min(16, max(1, owner_burst_tasks))) as executor:
            futures = [
                executor.submit(
                    api_services[index % len(api_services)].submit_generation,
                    {"id": owner_id},
                    client_task_id=f"owner-burst-task-{index}",
                    prompt="single owner burst",
                    model="gpt-image-2",
                    size=None,
                    base_url="http://load-test.invalid",
                )
                for index in range(owner_burst_tasks)
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result.get("status") == "queued":
                        submitted += 1
                except Exception:
                    submit_errors += 1

        worker_deadline = time.perf_counter() + worker_timeout

        def worker_loop(index: int, service: ImageTaskService) -> None:
            worker_id = f"load-worker-{index + 1}"
            last_touch = 0.0
            while time.perf_counter() < worker_deadline:
                now = time.perf_counter()
                if now - last_touch >= 1:
                    try:
                        queue.touch_worker(worker_id, timeout_secs=60)
                    except Exception:
                        pass
                    last_touch = now
                with terminal_lock:
                    if len(terminal_ids) >= total_unique_tasks:
                        return
                result = service.work_once(timeout_secs=1)
                if result is None:
                    continue
                if result.get("status") in TERMINAL_STATUSES:
                    task_id = str(result.get("id") or "")
                    if task_id:
                        with terminal_lock:
                            terminal_ids.add(task_id)

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(worker_loop, index, service) for index, service in enumerate(worker_services)]
            for future in futures:
                future.result()

        elapsed = time.perf_counter() - started
        tasks = _load_test_tasks(worker_services[0])
        counts = _status_counts(tasks)
        terminal_count = sum(counts.get(status, 0) for status in TERMINAL_STATUSES)
        failed = counts.get("error", 0)
        succeeded = counts.get("success", 0)
        failure_rate = round(failed / terminal_count, 4) if terminal_count else 0.0
        duplicate_ids = {str(result.get("id")) for result in duplicate_results}
        max_queue_depth = max([int(sample.get("queue_depth") or 0) for sample in samples] + [0])
        max_active_slots = max([int(sample.get("active_slots") or 0) for sample in samples] + [0])
        avg_active_slots = (
            sum(int(sample.get("active_slots") or 0) for sample in samples) / len(samples)
            if samples
            else 0.0
        )
        max_active_workers = max([int(sample.get("active_workers") or 0) for sample in samples] + [0])
        max_owner_queued = max([int(sample.get("owner_queued_tasks") or 0) for sample in samples] + [0])
        max_owner_running = max([int(sample.get("owner_running_tasks") or 0) for sample in samples] + [0])
        peak_slot_utilization = round(max_active_slots / total_concurrency, 4) if total_concurrency else 0.0
        avg_slot_utilization = round(avg_active_slots / total_concurrency, 4) if total_concurrency else 0.0
        effective_worker_capacity = max(1, min(args.workers, total_concurrency))
        worker_utilization_avg = round(avg_active_slots / effective_worker_capacity, 4)
        worker_utilization_peak = round(max_active_slots / effective_worker_capacity, 4)
        timed_out = terminal_count < total_unique_tasks
        recommendations = _build_recommendations(
            total_concurrency=total_concurrency,
            workers=args.workers,
            owner_concurrency=max(1, int(args.owner_concurrency)),
            max_queue_depth=max_queue_depth,
            avg_slot_utilization=avg_slot_utilization,
            peak_slot_utilization=peak_slot_utilization,
            max_owner_queued=max_owner_queued,
            failure_rate=failure_rate,
        )
        print(json.dumps({
            "scenario": {
                "users": args.users,
                "api_instances": args.api_instances,
                "workers": args.workers,
                "total_concurrency": total_concurrency,
                "owner_concurrency": max(1, int(args.owner_concurrency)),
                "owner_burst_tasks": owner_burst_tasks,
                "handler_delay_ms": args.handler_delay_ms,
                "fail_every": max(0, int(args.fail_every)),
                "upstream_called": False,
            },
            "submission": {
                "submitted_unique_tasks": submitted + 1,
                "submit_errors": submit_errors,
                "duplicate_probe_responses": len(duplicate_results),
                "duplicate_probe_task_ids": len(duplicate_ids),
            },
            "results": {
                "expected_unique_tasks": total_unique_tasks,
                "terminal_tasks": terminal_count,
                "success_tasks": succeeded,
                "failed_tasks": failed,
                "status_counts": counts,
                "failure_rate": failure_rate,
                "timed_out": timed_out,
                "handler_calls": calls[0],
            },
            "queue": {
                "max_depth": max_queue_depth,
                "final_depth": int(queue.queue_depth()),
                "max_active_slots": max_active_slots,
                "avg_active_slots": round(avg_active_slots, 3),
                "slot_limit": total_concurrency,
                "peak_slot_utilization": peak_slot_utilization,
                "avg_slot_utilization": avg_slot_utilization,
            },
            "workers": {
                "max_active_workers": max_active_workers,
                "worker_utilization_peak": worker_utilization_peak,
                "worker_utilization_avg": worker_utilization_avg,
            },
            "single_owner": {
                "owner_id": owner_id,
                "max_queued_tasks": max_owner_queued,
                "max_running_tasks": max_owner_running,
            },
            "elapsed_seconds": round(elapsed, 3),
            "throughput_tasks_per_second": round(terminal_count / elapsed, 2) if elapsed else None,
            "recommendations": recommendations,
            "database_url": database_url.split("@")[-1],
        }, ensure_ascii=False, indent=2))
        if timed_out:
            raise SystemExit(2)
    finally:
        sampler_stop.set()
        sampler.join(timeout=1)
        if worker_services:
            try:
                load_keys = _load_test_keys(worker_services[0])
                if load_keys:
                    worker_services[0].task_store.delete_keys(load_keys)
            except Exception:
                pass
        for service in [*api_services, *worker_services]:
            service.close()
        queue._client.delete(queue_name, queue._slot_key, queue._worker_key)
        if temp_context is not None:
            temp_context.cleanup()


if __name__ == "__main__":
    main()
